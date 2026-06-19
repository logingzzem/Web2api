"""内存任务存储与状态管理。"""

import threading
import uuid
from datetime import UTC, datetime

from app.schemas import TaskResult, TaskStatus


class Task:
    """任务对象。"""

    def __init__(self, task_id: str, form_data: dict, callback_url: str | None = None) -> None:
        self.task_id: str = task_id
        self.form_data: dict = form_data
        self.callback_url: str | None = callback_url
        self.status: TaskStatus = TaskStatus.PENDING
        self.result: TaskResult | None = None
        self.created_at: datetime = datetime.now(UTC)
        self.updated_at: datetime = datetime.now(UTC)


class TaskStore:
    """线程安全的内存任务存储。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, form_data: dict, callback_url: str | None = None) -> Task:
        """创建新任务。"""
        task_id = str(uuid.uuid4())
        task = Task(task_id=task_id, form_data=form_data, callback_url=callback_url)
        with self._lock:
            self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        """根据 task_id 查询任务。"""
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self) -> list[Task]:
        """返回所有任务，按创建时间倒序。"""
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: TaskResult | None = None,
    ) -> bool:
        """更新任务状态。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = status
            if result is not None:
                task.result = result
            task.updated_at = datetime.now(UTC)
            return True


# 全局单例
task_store = TaskStore()
