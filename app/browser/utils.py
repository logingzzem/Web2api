"""浏览器相关工具函数。"""

import logging
import os

logger = logging.getLogger(__name__)


def ensure_screenshot_dir(screenshot_dir: str) -> str:
    """确保截图保存目录存在，返回目录路径。"""
    os.makedirs(screenshot_dir, exist_ok=True)
    return screenshot_dir


def make_screenshot_path(screenshot_dir: str, task_id: str, suffix: str = "screenshot") -> str:
    """生成截图文件路径。

    Args:
        screenshot_dir: 截图保存目录
        task_id: 任务 ID
        suffix: 文件名后缀（如 success / failed）

    Returns:
        完整文件路径，如 /tmp/web2api/abc123_success.png
    """
    ensure_screenshot_dir(screenshot_dir)
    filename = f"{task_id}_{suffix}.png"
    return os.path.join(screenshot_dir, filename)


def extract_page_message(page) -> str:
    """从页面中提取结果消息（成功/失败提示文本）。"""
    # 优先查找常见成功/失败提示元素
    selectors = [
        ".alert-success",
        ".alert-danger",
        ".message",
        ".result-msg",
        "[class*='success']",
        "[class*='error']",
        "[class*='message']",
        "body",
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible():
                text = el.inner_text().strip()
                if text:
                    return text[:500]
        except Exception:
            continue
    return "未检测到明确结果消息，请查看截图"


def wait_for_result_page(page, timeout_ms: int = 5000) -> bool:
    """等待表单提交后的结果页面稳定（等待网络空闲）。"""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms / 1000)
        return True
    except Exception:
        logger.warning("等待网络空闲超时，继续执行")
        return False
