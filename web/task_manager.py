"""后台任务管理器：线程池执行 + 状态追踪 + 日志收集"""
from __future__ import annotations

import io
import logging
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .schemas import (
    LogEntry, TaskInfo, TaskStatus, WorkflowType, WORKFLOW_LABELS,
)


class ThreadSafeList:
    """线程安全的日志列表"""
    def __init__(self):
        self._lock = threading.Lock()
        self._items: list[LogEntry] = []

    def append(self, item: LogEntry):
        with self._lock:
            self._items.append(item)

    def get_all(self) -> list[LogEntry]:
        with self._lock:
            return list(self._items)

    def get_tail(self, n: int = 100) -> list[LogEntry]:
        with self._lock:
            return list(self._items[-n:])


class TaskLogHandler(logging.Handler):
    """把日志输出重定向到任务的日志列表"""
    def __init__(self, log_list: ThreadSafeList):
        super().__init__()
        self.log_list = log_list
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            entry = LogEntry(
                timestamp=datetime.now(),
                level=record.levelname.lower(),
                message=msg,
            )
            self.log_list.append(entry)
        except Exception:
            pass


class TaskContext:
    """单个任务的执行上下文，供 workflow 函数回调使用"""
    def __init__(self, task_id: str, name: str, workflow: WorkflowType, params: dict):
        self.task_id = task_id
        self.name = name
        self.workflow = workflow
        self.params = params
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.logs = ThreadSafeList()
        self.progress: float = 0.0
        self.current_step: Optional[str] = None
        self.draft_path: Optional[str] = None
        self.output_dir: Optional[str] = None
        self.error: Optional[str] = None
        self.confirm_data: Optional[dict[str, Any]] = None
        self._confirm_event = threading.Event()
        self._confirm_choice: Optional[str] = None
        self._confirm_edited_content: Optional[str] = None
        self._cancel_event = threading.Event()

    def set_progress(self, progress: float, step: Optional[str] = None):
        self.progress = min(100.0, max(0.0, progress))
        if step is not None:
            self.current_step = step

    def log(self, message: str, level: str = "info"):
        entry = LogEntry(
            timestamp=datetime.now(),
            level=level,
            message=message,
        )
        self.logs.append(entry)

    def wait_for_confirm(self, confirm_data: dict[str, Any]) -> tuple[str, Optional[str]]:
        """暂停任务等待用户确认，返回 (choice, edited_content)"""
        self.confirm_data = confirm_data
        self.status = TaskStatus.WAITING_CONFIRM
        self._confirm_event.clear()
        self._confirm_event.wait()  # 阻塞直到用户响应
        self.confirm_data = None
        if self.status == TaskStatus.CANCELLED:
            return ("n", None)
        self.status = TaskStatus.RUNNING
        return (self._confirm_choice, self._confirm_edited_content)

    def submit_confirm(self, choice: str, edited_content: Optional[str] = None):
        """用户提交确认结果"""
        self._confirm_choice = choice
        self._confirm_edited_content = edited_content
        self._confirm_event.set()

    def request_cancel(self):
        self._cancel_event.set()
        if self.status == TaskStatus.WAITING_CONFIRM:
            self._confirm_choice = "n"
            self._confirm_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def to_info(self, include_logs: bool = True, log_tail: int = 200) -> TaskInfo:
        return TaskInfo(
            task_id=self.task_id,
            name=self.name,
            workflow=self.workflow,
            workflow_label=WORKFLOW_LABELS[self.workflow],
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            params=self.params,
            draft_path=self.draft_path,
            output_dir=self.output_dir,
            error=self.error,
            progress=self.progress,
            current_step=self.current_step,
            logs=self.logs.get_tail(log_tail) if include_logs else [],
            confirm_data=self.confirm_data,
        )


class TaskManager:
    """全局任务管理器"""
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="video-task")
        self._tasks: dict[str, TaskContext] = {}
        self._lock = threading.Lock()
        self._workflow_handlers: dict[WorkflowType, Callable[[TaskContext], None]] = {}

    def register_workflow(self, wf_type: WorkflowType, handler: Callable[[TaskContext], None]):
        self._workflow_handlers[wf_type] = handler

    def create_task(
        self,
        workflow: WorkflowType,
        params: dict[str, Any],
        name: Optional[str] = None,
    ) -> TaskContext:
        task_id = uuid.uuid4().hex[:12]
        if name is None:
            name = f"{WORKFLOW_LABELS[workflow]}-{datetime.now().strftime('%m%d%H%M')}"
        ctx = TaskContext(task_id=task_id, name=name, workflow=workflow, params=params)
        with self._lock:
            self._tasks[task_id] = ctx
        return ctx

    def submit_task(self, ctx: TaskContext) -> Future:
        handler = self._workflow_handlers.get(ctx.workflow)
        if handler is None:
            raise ValueError(f"未注册的工作流: {ctx.workflow}")

        def _run():
            ctx.status = TaskStatus.RUNNING
            ctx.started_at = datetime.now()
            ctx.set_progress(0, "初始化")
            # 捕获stdout/stderr到日志
            stdout_capture = _StdoutCapture(ctx)
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout = stdout_capture
            sys.stderr = stdout_capture
            try:
                # 配置root logger让pipeline日志也流入
                root_logger = logging.getLogger()
                log_handler = TaskLogHandler(ctx.logs)
                root_logger.addHandler(log_handler)
                try:
                    handler(ctx)
                    if ctx.status != TaskStatus.CANCELLED:
                        ctx.status = TaskStatus.SUCCESS
                        ctx.set_progress(100, "完成")
                        ctx.log("✅ 任务完成！", "success")
                finally:
                    root_logger.removeHandler(log_handler)
            except Exception as e:
                import traceback
                ctx.error = f"{type(e).__name__}: {e}"
                ctx.status = TaskStatus.FAILED
                ctx.log(f"❌ 任务失败: {e}", "error")
                ctx.log(traceback.format_exc(), "error")
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                ctx.finished_at = datetime.now()

        return self._executor.submit(_run)

    def get_task(self, task_id: str) -> Optional[TaskContext]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[TaskContext]:
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def cancel_task(self, task_id: str) -> bool:
        ctx = self.get_task(task_id)
        if ctx is None:
            return False
        ctx.request_cancel()
        return True


class _StdoutCapture(io.TextIOBase):
    """把 print() 输出重定向到任务日志"""
    def __init__(self, ctx: TaskContext):
        self.ctx = ctx
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line.strip():
                self.ctx.log(line)
        return len(s)

    def flush(self):
        if self._buf.strip():
            self.ctx.log(self._buf)
            self._buf = ""


# 全局单例
task_manager = TaskManager(max_workers=2)
