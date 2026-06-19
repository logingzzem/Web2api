"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import (
    TaskListResponse,
    TaskResponse,
    TaskResult,
    TaskStatus,
    UploadRequest,
    UploadResponse,
)
from app.tasks import task_store

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_browser_task(task_id: str, form_data: dict[str, Any], callback_url: str | None) -> None:
    """在后台线程中执行浏览器自动化任务。"""
    from app.browser.agent import BrowserAgent  # 延迟导入，避免测试环境需要 playwright

    settings = get_settings()
    agent = BrowserAgent(settings)

    # 更新状态为 running
    task_store.update_status(task_id, TaskStatus.RUNNING)

    logger.info("开始执行任务 %s", task_id)
    result = agent.run(task_id, form_data)

    # 构建 TaskResult
    task_result = TaskResult(
        message=result.get("message", ""),
        screenshot_path=result.get("screenshot_path"),
        error=result.get("error"),
    )

    # 判断成功/失败
    final_status = TaskStatus.SUCCESS if result.get("error") is None else TaskStatus.FAILED
    task_store.update_status(task_id, final_status, task_result)

    logger.info("任务 %s 完成，状态=%s", task_id, final_status.value)

    # 触发回调
    if callback_url and final_status == TaskStatus.SUCCESS:
        _send_callback(callback_url, task_id, form_data, task_result)


def _send_callback(callback_url: str, task_id: str, form_data: dict, result: TaskResult) -> None:
    """发送回调通知（异步，不阻塞主流程）。"""
    try:
        payload = {
            "task_id": task_id,
            "form_data": form_data,
            "status": "success",
            "message": result.message,
            "screenshot_path": result.screenshot_path,
        }
        resp = httpx.post(callback_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("回调成功 [%s] <- %s", task_id, callback_url)
    except Exception as e:
        logger.warning("回调失败 [%s] -> %s: %s", task_id, callback_url, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期。"""
    settings = get_settings()
    logger.info("Web2API 服务启动，截图目录: %s", settings.screenshot_dir)
    yield
    logger.info("Web2API 服务关闭")


app = FastAPI(
    title="Web2API",
    description="使用 browser-use + Playwright 为会员网站表单上传提供 HTTP API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 中间件（按需开启）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/v1/upload", response_model=UploadResponse)
def upload_form(request: UploadRequest, background_tasks: BackgroundTasks) -> UploadResponse:
    """提交表单任务，返回 task_id。"""
    task = task_store.create(form_data=request.form_data, callback_url=request.callback_url)
    background_tasks.add_task(
        run_browser_task, task.task_id, request.form_data, request.callback_url
    )
    return UploadResponse(
        task_id=task.task_id,
        status=TaskStatus.PENDING,
        message="任务已提交，正在排队执行",
        created_at=task.created_at,
    )


@app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    """查询指定任务的状态和结果。"""
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        result=task.result,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@app.get("/api/v1/tasks", response_model=TaskListResponse)
def list_tasks(skip: int = 0, limit: int = 20) -> TaskListResponse:
    """查询所有任务（分页）。"""
    all_tasks = task_store.list_all()
    total = len(all_tasks)
    page = all_tasks[skip : skip + limit]
    return TaskListResponse(
        total=total,
        tasks=[
            TaskResponse(
                task_id=t.task_id,
                status=t.status,
                result=t.result,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in page
        ],
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    """健康检查接口。"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
