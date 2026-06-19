"""browser-use Agent 封装：登录 → 填表 → 提交流程。"""

import logging
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser.utils import (
    extract_page_message,
    make_screenshot_path,
    wait_for_result_page,
)
from app.config import Settings

logger = logging.getLogger(__name__)


class BrowserAgent:
    """浏览器自动化 Agent，负责完整的表单提交流程。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, task_id: str, form_data: dict[str, Any]) -> dict[str, Any]:
        """执行浏览器自动化流程。

        Args:
            task_id: 任务 ID，用于截图命名
            form_data: 表单数据字典，key 为 API 字段名

        Returns:
            包含 message / screenshot_path / error 的字典
        """
        timeout_s = self.settings.browser_timeout_ms / 1000
        screenshot_success = make_screenshot_path(self.settings.screenshot_dir, task_id, "success")
        screenshot_failed = make_screenshot_path(self.settings.screenshot_dir, task_id, "failed")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.settings.browser_headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = context.new_page()
            page.set_default_timeout(timeout_s * 1000)

            try:
                # Step 1: 登录
                self._login(page)
                # Step 2: 导航到表单页
                page.goto(self.settings.form_url, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle")
                # Step 3: 填写表单
                self._fill_form(page, form_data)
                # Step 4: 提交
                self._submit(page)
                # Step 5: 等待结果页稳定
                wait_for_result_page(page, timeout_ms=self.settings.browser_timeout_ms)
                # Step 6: 提取消息并截图
                message = extract_page_message(page)
                page.screenshot(path=screenshot_success, full_page=True)
                return {"message": message, "screenshot_path": screenshot_success, "error": None}

            except LoginFailedError as e:
                logger.error("登录失败: %s", e)
                try:
                    page.screenshot(path=screenshot_failed, full_page=True)
                except Exception:
                    pass
                return {
                    "message": f"登录失败：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "LoginFailed",
                }

            except FormFillError as e:
                logger.error("表单填写失败: %s", e)
                try:
                    page.screenshot(path=screenshot_failed, full_page=True)
                except Exception:
                    pass
                return {
                    "message": f"表单填写失败：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "FormFillError",
                }

            except SubmitError as e:
                logger.error("提交失败: %s", e)
                try:
                    page.screenshot(path=screenshot_failed, full_page=True)
                except Exception:
                    pass
                return {
                    "message": f"提交失败：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "SubmitError",
                }

            except Exception as e:
                logger.exception("浏览器执行异常: %s", e)
                try:
                    page.screenshot(path=screenshot_failed, full_page=True)
                except Exception:
                    pass
                return {
                    "message": f"执行异常：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "UnknownError",
                }

            finally:
                browser.close()

    def _login(self, page) -> None:
        """执行登录操作。"""
        logger.info("正在打开登录页: %s", self.settings.login_url)
        page.goto(self.settings.login_url, wait_until="domcontentloaded")

        # 填写用户名
        username_sel = self.settings.username_selector
        page.wait_for_selector(username_sel, state="visible", timeout=10000)
        page.fill(username_sel, self.settings.member_username)

        # 填写密码
        password_sel = self.settings.password_selector
        page.fill(password_sel, self.settings.member_password)

        # 点击登录按钮
        login_btn_sel = self.settings.login_button_selector
        page.click(login_btn_sel)

        # 等待跳转完成（等待 URL 变化或出现登出元素）
        try:
            page.wait_for_url("**", timeout=15000)
        except Exception:
            pass

        logger.info("登录完成，当前 URL: %s", page.url)

    def _fill_form(self, page, form_data: dict[str, Any]) -> None:
        """根据 form_data 和字段映射填写表单。"""
        field_selectors = self.settings.get_all_field_selectors()
        filled = []
        missing = []

        for field_name, field_value in form_data.items():
            selector = field_selectors.get(field_name)
            if not selector:
                # 尝试从配置中获取
                selector = self.settings.get_field_selector(field_name)

            if not selector:
                missing.append(field_name)
                logger.warning("字段 '%s' 未配置选择器，跳过填写", field_name)
                continue

            try:
                # 判断字段类型（textarea / select / checkbox / file 等）
                tag = page.locator(selector).evaluate_handle("el => el.tagName.toLowerCase()")
                tag_name = tag.evaluate("t => t")
                if tag_name == "select":
                    page.select_option(selector, str(field_value))
                elif tag_name == "textarea":
                    page.fill(selector, str(field_value))
                elif tag_name == "input":
                    input_type = page.locator(selector).evaluate(
                        "el => el.type ? el.type.toLowerCase() : 'text'"
                    )
                    if input_type in ("checkbox", "radio"):
                        if field_value:
                            page.check(selector)
                        else:
                            page.uncheck(selector)
                    elif input_type == "file":
                        page.set_input_files(selector, str(field_value))
                    else:
                        page.fill(selector, str(field_value))
                else:
                    page.fill(selector, str(field_value))

                filled.append(field_name)
                logger.info("字段 '%s' 已填写，selector=%s", field_name, selector)
            except Exception as e:
                missing.append(field_name)
                logger.error("填写字段 '%s' 失败: %s", field_name, e)

        if missing:
            logger.warning("以下字段未成功填写: %s", missing)

    def _submit(self, page) -> None:
        """点击提交按钮。"""
        submit_sel = self.settings.submit_button_selector
        page.wait_for_selector(submit_sel, state="visible", timeout=10000)
        page.click(submit_sel)
        logger.info("已点击提交按钮")


class LoginFailedError(Exception):
    """登录失败异常。"""

    pass


class FormFillError(Exception):
    """表单填写失败异常。"""

    pass


class SubmitError(Exception):
    """表单提交失败异常。"""

    pass
