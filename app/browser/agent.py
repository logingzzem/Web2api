"""browser-use Agent 封装：登录 → （多步点击导航） → 填表 → 提交。"""

import logging
from typing import Any

from playwright.sync_api import Page, sync_playwright

from app.browser.utils import (
    extract_page_message,
    make_screenshot_path,
    wait_for_result_page,
)
from app.config import Settings

logger = logging.getLogger(__name__)

# 智能按钮查找的所有候选策略（按顺序尝试，越靠前越精确）
# target 是按钮上的文字 / 文字片段
BUTTON_FIND_STRATEGIES: list[str] = [
    # 1) 精确 role+name（最可靠，避免匹配普通文本节点）
    "role_button",
    # 2) 在链接 (<a>) 中按文本查找（导航菜单常见）
    "role_link",
    # 3) 包含文本的任意可见元素（兜底方案）
    "has_text",
    # 4) CSS 选择器里直接包含文本的 attribute（如 value / title）
    "attr_value",
    # 5) aria-label 中含文本
    "aria_label",
]


class BrowserAgent:
    """浏览器自动化 Agent，负责完整的表单提交流程。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._timeout = settings.browser_timeout_ms

    def run(self, task_id: str, form_data: dict[str, Any]) -> dict[str, Any]:
        """执行浏览器自动化流程。"""
        screenshot_success = make_screenshot_path(self.settings.screenshot_dir, task_id, "success")
        screenshot_failed = make_screenshot_path(self.settings.screenshot_dir, task_id, "failed")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.settings.browser_headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = context.new_page()
            page.set_default_timeout(self._timeout)

            try:
                # Step 1: 登录
                self._login(page)
                # Step 2: 导航到表单（多步点击 或 直接 goto）
                self._navigate_to_form(page)
                # Step 3: 等待表单就绪
                self._wait_form_ready(page)
                # Step 4: 填写表单（CSS 选择器 + get_by_label 兜底）
                self._fill_form(page, form_data)
                # Step 5: 点击提交按钮（CSS 选择器 + 文本匹配兜底）
                self._submit(page)
                # Step 6: 等待结果页稳定 + 截图
                wait_for_result_page(page, timeout_ms=self._timeout)
                message = extract_page_message(page)
                page.screenshot(path=screenshot_success, full_page=True)
                return {"message": message, "screenshot_path": screenshot_success, "error": None}

            except LoginFailedError as e:
                logger.error("登录失败: %s", e)
                self._safe_screenshot(page, screenshot_failed)
                return {
                    "message": f"登录失败：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "LoginFailed",
                }

            except NavigationError as e:
                logger.error("导航失败: %s", e)
                self._safe_screenshot(page, screenshot_failed)
                return {
                    "message": f"导航失败：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "NavigationError",
                }

            except FormFillError as e:
                logger.error("表单填写失败: %s", e)
                self._safe_screenshot(page, screenshot_failed)
                return {
                    "message": f"表单填写失败：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "FormFillError",
                }

            except SubmitError as e:
                logger.error("提交失败: %s", e)
                self._safe_screenshot(page, screenshot_failed)
                return {
                    "message": f"提交失败：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "SubmitError",
                }

            except Exception as e:  # noqa: BLE001
                logger.exception("执行异常: %s", e)
                self._safe_screenshot(page, screenshot_failed)
                return {
                    "message": f"执行异常：{e}",
                    "screenshot_path": screenshot_failed,
                    "error": "UnknownError",
                }

            finally:
                browser.close()

    # ------------------------------------------------------------------
    # Step 1: 登录
    # ------------------------------------------------------------------
    def _login(self, page: Page) -> None:
        logger.info("① 打开登录页: %s", self.settings.login_url)
        page.goto(self.settings.login_url, wait_until="domcontentloaded")

        # 用户名：多选择器依次尝试
        username_val = self.settings.member_username
        username_sels = Settings.split_selectors(self.settings.username_selector)
        if not self._fill_any(page, username_sels, username_val):
            raise LoginFailedError("未找到用户名输入框")

        # 密码
        password_val = self.settings.member_password
        password_sels = Settings.split_selectors(self.settings.password_selector)
        if not self._fill_any(page, password_sels, password_val):
            raise LoginFailedError("未找到密码输入框")

        # 登录按钮：先 CSS 选择器，后文本匹配（"登录/Login/Sign in" 等）
        login_btn_sels = Settings.split_selectors(self.settings.login_button_selector)
        if not self._click_any_selector(page, login_btn_sels):
            for text in ["登录", "登 录", "Login", "Sign in", "Sign In"]:
                if self._find_and_click(page, text):
                    break
            else:
                raise LoginFailedError("未找到登录按钮")

        # 等待跳转
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        logger.info("登录完成，当前 URL: %s", page.url)

    # ------------------------------------------------------------------
    # Step 2: 导航到表单页面（核心新增逻辑）
    # ------------------------------------------------------------------
    def _navigate_to_form(self, page: Page) -> None:
        steps = self.settings.get_navigation_steps()

        # 路径 A：配置了 navigation_steps → 按文字按钮点击前进
        if steps:
            logger.info("② 进入导航模式，共 %d 步: %s", len(steps), steps)
            # 先进入落地页
            if self.settings.landing_url:
                page.goto(self.settings.landing_url, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle")

            for idx, target in enumerate(steps, start=1):
                logger.info("②-%d 查找并点击: %s", idx, target)
                if not self._find_and_click(page, target):
                    raise NavigationError(f"第 {idx} 步：找不到可点击的元素，目标文字='{target}'")
                # 点击后短暂等待网络/弹窗稳定
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:  # noqa: BLE001
                    pass
            logger.info("② 导航全部完成")
            return

        # 路径 B：旧模式（直接 goto form_url）
        if self.settings.form_url:
            logger.info("② 直接打开表单页: %s", self.settings.form_url)
            page.goto(self.settings.form_url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            return

        raise NavigationError("既未配置 navigation_steps，也未配置 form_url，无法定位表单")

    # ------------------------------------------------------------------
    # Step 3: 等待表单就绪
    # ------------------------------------------------------------------
    def _wait_form_ready(self, page: Page) -> None:
        ready_sel = self.settings.form_ready_selector
        if not ready_sel:
            # 无配置则等待网络空闲
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            return

        for sel in Settings.split_selectors(ready_sel):
            try:
                page.wait_for_selector(sel, state="visible", timeout=10000)
                logger.info("③ 表单已就绪 (选择器=%s)", sel)
                return
            except Exception:  # noqa: BLE001
                continue
        logger.warning("③ form_ready_selector 未找到，但继续尝试后续步骤")

    # ------------------------------------------------------------------
    # Step 4: 填写表单（CSS 选择器优先，get_by_label 作为兜底）
    # ------------------------------------------------------------------
    def _fill_form(self, page: Page, form_data: dict[str, Any]) -> None:
        field_selectors = self.settings.get_all_field_selectors()
        filled: list[str] = []
        missing: list[str] = []

        for field_name, field_value in form_data.items():
            selector = field_selectors.get(field_name) or self.settings.get_field_selector(
                field_name
            )

            locator = None
            # 策略 A：CSS 选择器（配置了 FORM_FIELD_xxx）
            if selector:
                try:
                    loc = page.locator(selector).first
                    if loc.is_visible(timeout=2000):
                        locator = loc
                except Exception:  # noqa: BLE001
                    locator = None

            # 策略 B：get_by_label 匹配（字段名本身常就是 label 文本）
            if locator is None:
                for label_text in self._label_candidates(field_name):
                    try:
                        loc = page.get_by_label(label_text, exact=False).first
                        if loc.is_visible(timeout=1500):
                            locator = loc
                            logger.info("字段 '%s' 使用 label 匹配: %s", field_name, label_text)
                            break
                    except Exception:  # noqa: BLE001
                        continue

            # 策略 C：get_by_placeholder 匹配
            if locator is None:
                for ph_text in self._label_candidates(field_name):
                    try:
                        loc = page.locator(f"[placeholder*='{ph_text}']").first
                        if loc.is_visible(timeout=1500):
                            locator = loc
                            logger.info("字段 '%s' 使用 placeholder 匹配: %s", field_name, ph_text)
                            break
                    except Exception:  # noqa: BLE001
                        continue

            if locator is None:
                missing.append(field_name)
                logger.warning("字段 '%s' 未在页面找到可填写的元素，跳过", field_name)
                continue

            try:
                self._fill_by_locator(page, locator, field_value)
                filled.append(field_name)
            except Exception as e:  # noqa: BLE001
                missing.append(field_name)
                logger.error("填写字段 '%s' 失败: %s", field_name, e)

        logger.info("④ 表单填写完成，成功 %d 个，跳过/失败 %d 个", len(filled), len(missing))
        if missing:
            logger.info("   失败/跳过字段: %s", missing)

    # ------------------------------------------------------------------
    # Step 5: 点击提交按钮
    # ------------------------------------------------------------------
    def _submit(self, page: Page) -> None:
        # 优先 CSS 选择器
        sels = Settings.split_selectors(self.settings.submit_button_selector)
        if self._click_any_selector(page, sels, raise_when_missing=False):
            logger.info("⑤ 已点击提交按钮（CSS 选择器）")
            return

        # 兜底：按文本匹配
        candidates = self.settings.get_submit_button_texts()
        for text in candidates:
            if self._find_and_click(page, text):
                logger.info("⑤ 已点击提交按钮（文本='%s'）", text)
                return

        raise SubmitError("所有提交按钮候选（CSS+文本）均未找到")

    # ==================================================================
    # 工具方法：智能查找与点击（核心方法）
    # ==================================================================
    def _find_and_click(self, page: Page, target_text: str) -> bool:
        """按多策略在页面中查找含目标文字的可点击元素并点击。

        策略顺序：
        1. page.get_by_role("button", name=target_text)
        2. page.get_by_role("link", name=target_text)
        3. 可见元素 innerText 包含 target_text → 取第一个
        4. [value=target_text] / [title=target_text] 等 attribute 匹配
        5. [aria-label 含 target_text]

        返回 True 表示至少有一次成功点击。
        """
        target = target_text.strip()
        if not target:
            return False

        # ---- 策略 1：role=button + 文本 ----
        try:
            loc = page.get_by_role("button", name=target).first
            if self._safe_visible_click(page, loc):
                logger.debug("   命中策略 role=button: %s", target)
                return True
        except Exception:  # noqa: BLE001
            pass

        # ---- 策略 2：role=link + 文本（用于菜单导航、a 标签按钮）----
        try:
            loc = page.get_by_role("link", name=target).first
            if self._safe_visible_click(page, loc):
                logger.debug("   命中策略 role=link: %s", target)
                return True
        except Exception:  # noqa: BLE001
            pass

        # ---- 策略 3：任意可见元素 innerText 包含目标 ----
        try:
            loc = page.locator(":scope").filter(has_text=target).first
            if self._safe_visible_click(page, loc):
                logger.debug("   命中策略 has_text: %s", target)
                return True
        except Exception:  # noqa: BLE001
            pass

        # ---- 策略 4：value / title attribute ----
        for attr in ["value", "title"]:
            try:
                loc = page.locator(f"[{attr}*='{target}']").first
                if self._safe_visible_click(page, loc):
                    logger.debug("   命中策略 attr-%s: %s", attr, target)
                    return True
            except Exception:  # noqa: BLE001
                continue

        # ---- 策略 5：aria-label ----
        try:
            loc = page.locator(f"[aria-label*='{target}']").first
            if self._safe_visible_click(page, loc):
                logger.debug("   命中策略 aria-label: %s", target)
                return True
        except Exception:  # noqa: BLE001
            pass

        logger.warning("   未找到任何可点击的目标: %s", target)
        return False

    def _safe_visible_click(self, page: Page, locator) -> bool:
        """确保元素可见后点击，避免点到不可用/不可见元素。"""
        try:
            locator.wait_for(state="visible", timeout=5000)
            if not locator.is_visible():
                return False
            locator.click(timeout=5000)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("   click 失败: %s", e)
            return False

    def _click_any_selector(
        self, page: Page, selectors: list[str], raise_when_missing: bool = False
    ) -> bool:
        """按顺序尝试多个 CSS 选择器，点击第一个可见且可点击的。"""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=5000)
                if loc.is_visible():
                    loc.click(timeout=5000)
                    return True
            except Exception:  # noqa: BLE001
                continue
        if raise_when_missing:
            raise SubmitError(f"找不到提交按钮，选择器: {selectors}")
        return False

    def _fill_any(self, page: Page, selectors: list[str], value: str) -> bool:
        """按顺序尝试多个选择器填写文本。"""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=5000)
                if loc.is_visible():
                    loc.fill(value)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _fill_by_locator(self, page: Page, locator, value: Any) -> None:
        """根据 locator 判断元素类型并写入。"""
        try:
            tag_name = locator.evaluate("el => el.tagName ? el.tagName.toLowerCase() : ''")
        except Exception:  # noqa: BLE001
            tag_name = ""

        if tag_name == "select":
            locator.select_option(str(value))
        elif tag_name == "textarea":
            locator.fill(str(value))
        elif tag_name == "input":
            input_type = locator.evaluate("el => el.type ? el.type.toLowerCase() : 'text'")
            if input_type in ("checkbox", "radio"):
                if value:
                    locator.check()
                else:
                    locator.uncheck()
            elif input_type == "file":
                locator.set_input_files(str(value))
            else:
                locator.fill(str(value))
        else:
            # 非标准输入（如 contenteditable 的富文本）
            locator.fill(str(value))

    @staticmethod
    def _label_candidates(field_name: str) -> list[str]:
        """为字段名生成若干 label/占位符候选，供 get_by_label 与 placeholder 匹配。"""
        base = field_name.strip().lower()
        # 中文场景：直接返回原名 + 常用后缀
        candidates = [field_name, field_name.replace("_", ""), field_name.replace("_", " ")]
        # 英文场景：snake_case → Title Case / words
        words_cn = base.replace("_", " ").replace("-", " ")
        candidates.append(words_cn.title())
        candidates.append(words_cn)
        # 去重保持顺序
        seen: set[str] = set()
        result: list[str] = []
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                result.append(c)
        return result[:6]

    @staticmethod
    def _safe_screenshot(page: Page, path: str) -> None:
        try:
            page.screenshot(path=path, full_page=True)
        except Exception:  # noqa: BLE001
            pass


class LoginFailedError(Exception):
    """登录失败。"""

    pass


class NavigationError(Exception):
    """导航/点击按钮失败。"""

    pass


class FormFillError(Exception):
    """表单填写失败。"""

    pass


class SubmitError(Exception):
    """表单提交失败。"""

    pass
