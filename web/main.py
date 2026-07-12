"""FastAPI Web 后端主入口
启动：python -m uvicorn web.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import io
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import cv2
import numpy as np
from fastapi import FastAPI, Form, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from pipeline.helpers import load_env
from pipeline import ai_watermark_remover

from .schemas import (
    ConfirmRequest, FileInfo, TaskCreateRequest, TaskCreateResponse,
    TaskInfo, TaskListResponse, TaskStatus, WorkflowType,
    WORKFLOW_DESCRIPTIONS, WORKFLOW_LABELS, WORKFLOW_PARAMS,
)
from .task_manager import task_manager, TaskContext
from .workflows import register_all_workflows

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_env(PROJECT_ROOT / ".env")
UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# 注册所有工作流
register_all_workflows(task_manager)

app = FastAPI(title="AI 视频生成工作台", version="1.0.0")

# CORS（开发阶段允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
#  首页 & 静态前端文件
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


# ---------------------------------------------------------------------------
#  工作流元信息
# ---------------------------------------------------------------------------
@app.get("/api/workflows")
def list_workflows():
    """列出所有可用工作流及参数Schema"""
    result = []
    for wf in WorkflowType:
        params_model = WORKFLOW_PARAMS[wf]
        schema = params_model.model_json_schema()
        result.append({
            "id": wf.value,
            "name": wf.value,
            "label": WORKFLOW_LABELS[wf],
            "description": WORKFLOW_DESCRIPTIONS[wf],
            "params_schema": schema,
        })
    return result


# ---------------------------------------------------------------------------
#  任务管理
# ---------------------------------------------------------------------------
@app.post("/api/tasks", response_model=TaskCreateResponse)
def create_task(req: TaskCreateRequest):
    """提交新任务"""
    # 校验参数
    params_model = WORKFLOW_PARAMS.get(req.workflow)
    if params_model is None:
        raise HTTPException(400, f"未知工作流: {req.workflow}")
    try:
        params_model(**req.params)
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors())

    ctx = task_manager.create_task(
        workflow=req.workflow,
        params=req.params,
        name=req.name,
    )
    task_manager.submit_task(ctx)
    return TaskCreateResponse(task_id=ctx.task_id, status=ctx.status)


@app.get("/api/tasks", response_model=TaskListResponse)
def list_tasks(limit: int = 50):
    """任务列表"""
    tasks = task_manager.list_tasks(limit=limit)
    return TaskListResponse(
        tasks=[t.to_info(include_logs=False) for t in tasks],
        total=len(tasks),
    )


@app.get("/api/tasks/{task_id}", response_model=TaskInfo)
def get_task(task_id: str, log_tail: int = 300):
    """任务详情（含最近日志）"""
    ctx = task_manager.get_task(task_id)
    if ctx is None:
        raise HTTPException(404, "任务不存在")
    return ctx.to_info(log_tail=log_tail)


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """取消任务"""
    ctx = task_manager.get_task(task_id)
    if ctx is None:
        raise HTTPException(404, "任务不存在")
    task_manager.cancel_task(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/confirm")
def confirm_task(task_id: str, req: ConfirmRequest):
    """提交确认卡点的选择"""
    ctx = task_manager.get_task(task_id)
    if ctx is None:
        raise HTTPException(404, "任务不存在")
    if ctx.status != TaskStatus.WAITING_CONFIRM:
        raise HTTPException(400, "任务不在等待确认状态")
    ctx.submit_confirm(req.choice, req.edited_content)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/confirm")
def confirm_task(task_id: str, req: ConfirmRequest):
    """提交确认卡点的选择（预留接口，当前所有工作流默认-y自动确认）"""
    ctx = task_manager.get_task(task_id)
    if ctx is None:
        raise HTTPException(404, "任务不存在")
    ctx.submit_confirm(req.choice, req.edited_content)
    return {"ok": True}


# ---------------------------------------------------------------------------
#  文件上传
# ---------------------------------------------------------------------------
@app.post("/api/upload", response_model=FileInfo)
async def upload_file(file: UploadFile = File(...)):
    """上传文件（图片/音频/PDF/视频），返回可直接在参数中使用的本地路径"""
    file_id = uuid.uuid4().hex[:12]
    # 安全文件名：保留原始扩展名
    original_name = file.filename or "upload.bin"
    ext = Path(original_name).suffix
    safe_name = f"{file_id}{ext}"
    dest = UPLOAD_DIR / safe_name

    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    size = dest.stat().st_size
    return FileInfo(
        file_id=file_id,
        filename=original_name,
        path=str(dest.absolute()),
        size=size,
        uploaded_at=datetime.now(),
    )


@app.get("/api/files")
def list_uploaded_files():
    """已上传文件列表"""
    files = []
    for p in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and not p.name.startswith("."):
            files.append({
                "filename": p.name,
                "path": str(p.absolute()),
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            })
    return files


@app.get("/api/files/{filename}")
def get_uploaded_file(filename: str):
    """访问已上传文件（用于预览）"""
    # 防止路径遍历
    safe_name = Path(filename).name
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(file_path))


# ---------------------------------------------------------------------------
#  图片工具 - 去水印
# ---------------------------------------------------------------------------
@app.get("/api/tools/watermark/config")
def watermark_config():
    """返回去水印工具配置（告诉前端哪些算法可用）"""
    # 检测云端API配置
    has_cloud = bool(os.environ.get("VOLC_ACCESS_KEY")) and bool(os.environ.get("VOLC_SECRET_KEY"))

    return {
        "methods": [
            {"id": "telea", "label": "OpenCV TELEA", "type": "traditional", "desc": "速度最快，适合小面积纯色背景水印", "available": True},
            {"id": "ns", "label": "OpenCV NS", "type": "traditional", "desc": "边缘衔接更自然，速度稍慢", "available": True},
            {"id": "cloud", "label": "AI 云端 (火山引擎)", "type": "ai-cloud", "desc": "商用级擦除，效果最佳，需配置AK/SK", "available": has_cloud},
        ]
    }


@app.post("/api/tools/remove-watermark")
async def api_remove_watermark(
    file: UploadFile = File(...),
    regions: str = Form(..., description="水印区域JSON，格式: [{x,y,w,h}, ...]"),
    method: str = Form("telea", description="修复算法: telea/ns/lama/cloud"),
    radius: int = Form(5, description="inpaint半径(仅传统算法)", ge=1, le=50),
    padding: int = Form(3, description="区域扩展像素", ge=0, le=30),
):
    """图片去水印：上传图片并标注水印区域，返回处理后的图片"""
    try:
        regions_list: list[dict[str, int]] = json.loads(regions)
    except json.JSONDecodeError:
        raise HTTPException(400, "regions参数不是有效的JSON")

    if not isinstance(regions_list, list) or len(regions_list) == 0:
        raise HTTPException(400, "请至少标注一个水印区域")

    for r in regions_list:
        if not all(k in r for k in ("x", "y", "w", "h")):
            raise HTTPException(400, "区域格式错误，每个区域需要 x, y, w, h 字段")

    if method not in ("telea", "ns", "cloud"):
        raise HTTPException(400, "method不支持，可选: telea/ns/cloud")

    # 读取上传图片
    content = await file.read()
    nparr = np.frombuffer(content, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "无法解析图片，请确保上传的是有效图片文件")

    h, w = img.shape[:2]

    try:
        if method in ("telea", "ns"):
            # 传统 OpenCV 算法
            mask = np.zeros((h, w), dtype=np.uint8)
            for r in regions_list:
                x = max(0, int(r["x"]) - padding)
                y = max(0, int(r["y"]) - padding)
                x2 = min(w, int(r["x"]) + int(r["w"]) + padding)
                y2 = min(h, int(r["y"]) + int(r["h"]) + padding)
                mask[y:y2, x:x2] = 255
            flags = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
            result = cv2.inpaint(img, mask, radius, flags)

        elif method == "cloud":
            # 火山引擎云端API
            from pipeline import ai_watermark_remover as aiwm
            result = aiwm.remove_watermark_cloud(
                img, regions_list, padding=padding
            )

    except RuntimeError as e:
        raise HTTPException(500, str(e))
    except Exception as e:
        raise HTTPException(500, f"处理失败: {type(e).__name__}: {e}")

    # 编码输出
    content_type = file.content_type or "image/png"
    if "jpeg" in content_type or "jpg" in content_type:
        ext = ".jpg"
        encode_param = [cv2.IMWRITE_JPEG_QUALITY, 95]
        media_type = "image/jpeg"
    elif "webp" in content_type:
        ext = ".webp"
        encode_param = [cv2.IMWRITE_WEBP_QUALITY, 95]
        media_type = "image/webp"
    else:
        ext = ".png"
        encode_param = [cv2.IMWRITE_PNG_COMPRESSION, 3]
        media_type = "image/png"

    success, buf = cv2.imencode(ext, result, encode_param)
    if not success:
        raise HTTPException(500, "图片编码失败")

    return StreamingResponse(
        io.BytesIO(buf.tobytes()),
        media_type=media_type,
        headers={"X-Content-Width": str(w), "X-Content-Height": str(h)},
    )


# ---------------------------------------------------------------------------
#  打开草稿目录（Windows）
# ---------------------------------------------------------------------------
@app.post("/api/tasks/{task_id}/open-folder")
def open_draft_folder(task_id: str):
    """在资源管理器中打开剪映草稿目录"""
    ctx = task_manager.get_task(task_id)
    if ctx is None:
        raise HTTPException(404, "任务不存在")
    folder = ctx.draft_path
    if not folder:
        # 尝试从日志中提取或使用默认输出目录
        folder = str(OUTPUTS_DIR)
    if not os.path.exists(folder):
        raise HTTPException(404, f"目录不存在: {folder}")
    try:
        os.startfile(folder)  # Windows only
        return {"ok": True, "path": folder}
    except Exception as e:
        raise HTTPException(500, f"打开失败: {e}")


# ---------------------------------------------------------------------------
#  前端静态文件托管（打包后）
# ---------------------------------------------------------------------------
FRONTEND_DIST = PROJECT_ROOT / "web-ui" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    # SPA fallback
    @app.exception_handler(404)
    async def spa_fallback(request, exc):
        if not request.url.path.startswith("/api"):
            index_path = FRONTEND_DIST / "index.html"
            if index_path.exists():
                return FileResponse(str(index_path))
        raise exc
