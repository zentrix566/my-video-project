"""API 请求/响应数据模型"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
#  工作流类型枚举
# ---------------------------------------------------------------------------
class WorkflowType(str, Enum):
    TOPIC = "topic"            # 主题 → 历史/人物介绍片
    MEME = "meme"              # 本地图片 → 梗图/图集片
    CAROUSEL = "carousel"      # 卡片轮播 + BGM 短视频
    CODE_WALK = "code_walk"    # 前端项目 → 代码走读片
    DOC_VIDEO = "doc_video"    # PDF+视频 → 需求讲解片
    NARRATION = "narration"    # 视频 → 录屏讲解片


WORKFLOW_LABELS: dict[WorkflowType, str] = {
    WorkflowType.TOPIC: "主题AI生成片",
    WorkflowType.MEME: "梗图/图集视频",
    WorkflowType.CAROUSEL: "卡片轮播短视频",
    WorkflowType.CODE_WALK: "代码走读视频",
    WorkflowType.DOC_VIDEO: "需求讲解视频",
    WorkflowType.NARRATION: "录屏讲解视频",
}

WORKFLOW_DESCRIPTIONS: dict[WorkflowType, str] = {
    WorkflowType.TOPIC: "输入主题，AI自动写稿+生图+配音+字幕，生成历史/人物介绍短视频",
    WorkflowType.MEME: "上传本地图片，自动生成带模糊背景+BGM的图集视频（零费用）",
    WorkflowType.CAROUSEL: "图片或JSON数据，自动生成横向滚动卡片轮播短视频（零费用）",
    WorkflowType.CODE_WALK: "指定前端项目路径，自动抓取UI+生成讲稿+配音，制作代码走读视频",
    WorkflowType.DOC_VIDEO: "上传PDF文档+录屏视频，AI理解画面生成讲解配音",
    WorkflowType.NARRATION: "上传录屏视频，AI理解画面自动生成讲解配音和字幕",
}


# ---------------------------------------------------------------------------
#  任务状态
# ---------------------------------------------------------------------------
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_CONFIRM = "waiting_confirm"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
#  各工作流参数
# ---------------------------------------------------------------------------
class TopicParams(BaseModel):
    """主题 → 历史/人物介绍片"""
    topic: str = Field(..., description="主题短语", min_length=1)
    brief: Optional[str] = Field(None, description="侧重点说明")
    style: Literal["epic", "documentary", "shorts", "codewalk"] = Field("epic", description="视觉风格")
    scenes: int = Field(8, description="场景数量", ge=3, le=20)
    speaker: Optional[str] = Field(None, description="TTS音色")
    dry_run: bool = Field(False, description="仅估算费用不实际运行")
    resume_latest: bool = Field(False, description="从最新断点续跑")
    skip_llm: bool = Field(False, description="跳过LLM写稿")
    skip_image: bool = Field(False, description="跳过图片生成")
    skip_tts: bool = Field(False, description="跳过TTS配音")
    skip_titles: bool = Field(False, description="跳过标题生成")
    auto_confirm: bool = Field(True, description="自动确认卡点（不中途暂停）")


class MemeParams(BaseModel):
    """本地图片 → 梗图/图集片"""
    source: Optional[str] = Field(None, description="图片目录路径")
    uploaded_images: Optional[list[str]] = Field(None, description="上传的图片文件路径列表")
    bgm: Optional[str] = Field(None, description="BGM音频文件路径")
    fit_to_bgm: bool = Field(True, description="视频时长适配BGM长度")
    count: int = Field(30, description="最多挑选图片数量", ge=1, le=200)
    range: Optional[str] = Field(None, description="图片范围，如 1-20")
    sort: Literal["name", "newest", "random"] = Field("name", description="图片排序方式")
    canvas_w: int = Field(1080, description="画布宽度")
    canvas_h: int = Field(1080, description="画布高度")
    fit_mode: Literal["contain", "cover"] = Field("contain", description="图片适配模式")
    movement: bool = Field(False, description="开启运镜")
    recursive: bool = Field(False, description="递归扫描子目录")
    bgm_volume: float = Field(0.8, description="BGM音量 0-1", ge=0.0, le=1.0)
    seconds_per_image: float = Field(3.0, description="每张图片停留秒数", ge=0.5, le=10.0)
    auto_confirm: bool = Field(True, description="自动确认")


class CarouselParams(BaseModel):
    """卡片轮播 + BGM 短视频"""
    source: Optional[str] = Field(None, description="图片目录路径（三种模式选一）")
    data: Optional[dict[str, Any]] = Field(None, description="JSON卡片数据（三种模式选一）")
    uploaded_images: Optional[list[str]] = Field(None, description="上传的图片文件路径列表（三种模式选一）")
    bgm: Optional[str] = Field(None, description="BGM音频文件路径")
    fit_to_bgm: bool = Field(True, description="视频时长适配BGM长度")
    seconds_per_card: float = Field(3.0, description="每张卡片停留秒数", ge=0.5, le=10.0)
    duration: Optional[float] = Field(None, description="总时长秒数（指定后覆盖seconds_per_card）")
    canvas_w: int = Field(1920, description="画布宽度")
    canvas_h: int = Field(1080, description="画布高度")
    cards_visible: float = Field(3.5, description="同时可见卡片数量", ge=1.0, le=8.0)
    bg_color: str = Field("#18181c", description="背景颜色")
    direction: Literal["left", "right"] = Field("left", description="滚动方向")
    bg_blur: bool = Field(False, description="用首张图模糊背景")
    card_radius: int = Field(18, description="卡片圆角")
    card_gap: int = Field(30, description="卡片间距")
    strip_height: float = Field(0.85, description="条带高度占画布比例", ge=0.3, le=1.0)
    no_text: bool = Field(False, description="不在卡片上渲染文字（图片自带UI时）")
    title_size_px: int = Field(42, description="标题字号")
    subtitle_size_px: int = Field(26, description="副标题字号")
    bgm_volume: float = Field(0.8, description="BGM音量 0-1", ge=0.0, le=1.0)
    count: int = Field(30, description="最多挑选图片数量", ge=1, le=200)
    sort: Literal["name", "newest", "random"] = Field("name", description="图片排序方式")
    recursive: bool = Field(False, description="递归扫描子目录")
    auto_confirm: bool = Field(True, description="自动确认")


class CodeWalkParams(BaseModel):
    """前端项目 → 代码走读片"""
    project: str = Field(..., description="前端项目本地路径")
    brief: Optional[str] = Field(None, description="讲解侧重点")
    scenes: int = Field(8, description="场景数量", ge=4, le=12)
    dev_port: int = Field(5173, description="开发服务器端口")
    skip_dev_server: bool = Field(False, description="跳过自动启动dev server")
    speaker: Optional[str] = Field(None, description="TTS音色")
    dry_run: bool = Field(False, description="仅估算费用")
    resume_latest: bool = Field(False, description="从最新断点续跑")
    skip_scan: bool = Field(False, description="跳过项目扫描")
    skip_llm: bool = Field(False, description="跳过LLM讲稿生成")
    skip_shots: bool = Field(False, description="跳过截图/渲染")
    skip_tts: bool = Field(False, description="跳过TTS配音")
    auto_confirm: bool = Field(True, description="自动确认卡点")


class DocVideoParams(BaseModel):
    """PDF+视频 → 需求讲解片"""
    pdf: str = Field(..., description="PDF文件路径")
    mp4: str = Field(..., description="录屏视频文件路径")
    brief: Optional[str] = Field(None, description="讲解侧重点")
    scenes: int = Field(8, description="场景数量", ge=3, le=20)
    speaker: Optional[str] = Field(None, description="TTS音色")
    no_vision: bool = Field(False, description="跳过视觉大模型（盲讲）")
    resume_latest: bool = Field(False, description="从最新断点续跑")
    skip_parse: bool = Field(False, description="跳过PDF/视频解析")
    skip_vision: bool = Field(False, description="跳过视觉理解")
    skip_llm: bool = Field(False, description="跳过讲稿生成")
    skip_cut: bool = Field(False, description="跳过视频切段")
    skip_tts: bool = Field(False, description="跳过TTS配音")
    auto_confirm: bool = Field(True, description="自动确认卡点")


class NarrationParams(BaseModel):
    """视频 → 录屏讲解片"""
    mp4: str = Field(..., description="录屏视频文件路径")
    brief: Optional[str] = Field(None, description="背景提示")
    scenes: int = Field(8, description="场景数量", ge=3, le=20)
    speaker: Optional[str] = Field(None, description="TTS音色")
    no_vision: bool = Field(False, description="跳过视觉大模型（盲讲）")
    auto_confirm: bool = Field(True, description="自动确认卡点")


WORKFLOW_PARAMS = {
    WorkflowType.TOPIC: TopicParams,
    WorkflowType.MEME: MemeParams,
    WorkflowType.CAROUSEL: CarouselParams,
    WorkflowType.CODE_WALK: CodeWalkParams,
    WorkflowType.DOC_VIDEO: DocVideoParams,
    WorkflowType.NARRATION: NarrationParams,
}


# ---------------------------------------------------------------------------
#  任务提交
# ---------------------------------------------------------------------------
class TaskCreateRequest(BaseModel):
    workflow: WorkflowType
    name: Optional[str] = Field(None, description="任务名称（可选，默认自动生成）")
    params: dict[str, Any]


class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskStatus


# ---------------------------------------------------------------------------
#  任务信息 & 日志
# ---------------------------------------------------------------------------
class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    message: str


class TaskInfo(BaseModel):
    task_id: str
    name: str
    workflow: WorkflowType
    workflow_label: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    params: dict[str, Any]
    draft_path: Optional[str] = None
    output_dir: Optional[str] = None
    error: Optional[str] = None
    progress: float = Field(0.0, description="进度 0-100")
    current_step: Optional[str] = None
    logs: list[LogEntry] = []
    confirm_data: Optional[dict[str, Any]] = Field(None, description="等待用户确认的数据")


class TaskListResponse(BaseModel):
    tasks: list[TaskInfo]
    total: int


# ---------------------------------------------------------------------------
#  确认卡点
# ---------------------------------------------------------------------------
class ConfirmRequest(BaseModel):
    choice: Literal["y", "n", "e"] = Field(..., description="y=继续, n=中止, e=编辑后继续")
    edited_content: Optional[str] = Field(None, description="编辑后的内容（choice=e时使用）")


# ---------------------------------------------------------------------------
#  文件上传
# ---------------------------------------------------------------------------
class UploadResponse(BaseModel):
    file_id: str
    filename: str
    path: str
    size: int
    content_type: str


class FileInfo(BaseModel):
    file_id: str
    filename: str
    path: str
    size: int
    uploaded_at: datetime
