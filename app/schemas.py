"""Pydantic 请求/响应模型定义。"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    """任务状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class UploadRequest(BaseModel):
    """POST /api/v1/upload 请求体。"""

    form_data: dict[str, Any] = Field(
        ..., description="表单字段字典，key 为 API 字段名，value 为字段值"
    )
    callback_url: str | None = Field(None, description="可选，回调通知 URL")


class UploadResponse(BaseModel):
    """POST /api/v1/upload 响应。"""

    task_id: str
    status: TaskStatus
    message: str
    created_at: datetime


class TaskResult(BaseModel):
    """任务执行结果。"""

    message: str
    screenshot_path: str | None = None
    error: str | None = None


class TaskResponse(BaseModel):
    """GET /api/v1/tasks/{task_id} 响应。"""

    task_id: str
    status: TaskStatus
    result: TaskResult | None = None
    created_at: datetime
    updated_at: datetime


class TaskListItem(BaseModel):
    """任务列表项。"""

    task_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    """GET /api/v1/tasks 响应。"""

    total: int
    tasks: list[TaskResponse]
