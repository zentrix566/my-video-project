"""FastAPI Web 后端主入口
启动：python -m uvicorn web.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .schemas import (
    ConfirmRequest, FileInfo, TaskCreateRequest, TaskCreateResponse,
    TaskInfo, TaskListResponse, TaskStatus, WorkflowType,
    WORKFLOW_DESCRIPTIONS, WORKFLOW_LABELS, WORKFLOW_PARAMS,
)
from .task_manager import task_manager, TaskContext
from .workflows import register_all_workflows

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
