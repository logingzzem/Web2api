"""
基于 Scrapling + PaddleOCR 的自动登录程序
支持图形验证码识别

用法：
    1. 修改下面的 CONFIG 配置（URL、用户名、密码、CSS选择器）
    2. python auto_login.py

依赖：
    pip install scrapling paddleocr
"""

import os
import re
import time
import logging
from io import BytesIO
from urllib.parse import urljoin

# 屏蔽 PaddleOCR 的 SourceCheck 日志
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "1"

from PIL import Image
from scrapling import Fetcher

# PaddleOCR 懒加载（避免未使用时加载模型）
_ocr = None
def _get_ocr():
    global _ocr
    if _ocr is None:
        from paddleocr import PaddleOCR
        _ocr = PaddleOCR(lang="ch", use_textline_orientation=False)
    return _ocr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ============================================================
# 配置区 —— 根据目标网站修改
# ============================================================
CONFIG = {
    # -------- 登录信息 --------
    "url": "https://example.com/login",  # 登录页面 URL
    "username": "your_username",
    "password": "your_password",

    # -------- 表单字段 CSS 选择器 --------
    "username_selector": "#username",      # 用户名输入框
    "password_selector": "#password",      # 密码输入框
    "captcha_selector": "#captcha-img",    # 验证码图片 <img> 元素
    "captcha_input_selector": "#captcha",  # 验证码输入框
    "submit_selector": "#login-btn",       # 提交按钮

    # -------- 验证码图片来源 --------
    # "src"      – 从 <img> 的 src 属性获取 (默认)
    # "base64"   – 从 <img> 的 src="data:image/..." 中提取
    # "css_bg"   – 从元素的 background-image 中提取
    "captcha_source": "src",

    # -------- 登录成功判断 --------
    # 登录后页面如果包含以下任一文本，视为成功
    "success_keywords": ["登录成功", "欢迎", "dashboard", "logout", "退出"],

    # -------- Scrapling 设置 --------
    "headless": True,
    "timeout": 30,

    # -------- 重试设置 --------
    "max_retries": 3,
}


class LoginBot:
    """基于 Scrapling + PaddleOCR 的自动登录机器人"""

    def __init__(self, config: dict):
        self.config = config
        self.session = None
        self.ocr = None

    # --------------------------------------------------
    # 初始化
    # --------------------------------------------------
    def init_session(self):
        """初始化 Scrapling 会话"""
        log.info("初始化 Scrapling 会话 ...")
        Fetcher.configure(impersonate="chrome_120")
        self.session = Fetcher()
        log.info("会话已创建")

    def init_ocr(self):
        """初始化 PaddleOCR (只加载一次)"""
        if self.ocr is None:
            log.info("加载 PaddleOCR 模型（首次加载较慢）...")
            self.ocr = _get_ocr()
            log.info("PaddleOCR 就绪")
        return self.ocr

    # --------------------------------------------------
    # 获取登录页面
    # --------------------------------------------------
    def fetch_login_page(self):
        """获取登录页面 HTML"""
        cfg = self.config
        log.info("正在请求登录页面: %s", cfg["url"])
        resp = self.session.get(cfg["url"], timeout=cfg["timeout"])
        log.info("页面状态码: %s", resp.status)
        if resp.status != 200:
            raise RuntimeError(f"页面请求失败, 状态码: {resp.status}")
        return resp

    # --------------------------------------------------
    # 提取验证码图片
    # --------------------------------------------------
    def extract_captcha_image(self, page) -> Image.Image | None:
        """根据配置从页面提取验证码图片"""
        cfg = self.config
        img_el = page.css(cfg["captcha_selector"])
        if not img_el:
            log.warning("未找到验证码元素: %s", cfg["captcha_selector"])
            return None

        img_url = None
        source = cfg.get("captcha_source", "src")

        if source == "src":
            img_url = img_el[0].attrib.get("src", "")
        elif source == "base64":
            src = img_el[0].attrib.get("src", "")
            if src.startswith("data:image"):
                img_url = src
        elif source == "css_bg":
            style = img_el[0].attrib.get("style", "")
            m = re.search(r'url\(["\']?(.*?)["\']?\)', style)
            if m:
                img_url = m.group(1)

        if not img_url:
            log.warning("无法获取验证码图片 URL")
            return None

        log.info("验证码图片 URL: %s", img_url[:80])

        # 处理 base64 内嵌图片
        if img_url.startswith("data:image"):
            import base64
            b64_data = img_url.split(",", 1)[1]
            raw = base64.b64decode(b64_data)
            return Image.open(BytesIO(raw))

        # 处理相对 URL
        if not img_url.startswith("http"):
            img_url = urljoin(self.config["url"], img_url)
            log.info("拼接后 URL: %s", img_url)

        # 通过 session 下载图片
        img_resp = self.session.get(img_url, timeout=self.config["timeout"])
        if img_resp.status != 200:
            log.warning("验证码图片下载失败, 状态码: %s", img_resp.status)
            return None
        return Image.open(BytesIO(img_resp.body))

    # --------------------------------------------------
    # OCR 识别验证码
    # --------------------------------------------------
    def recognize_captcha(self, img: Image.Image) -> str:
        """使用 PaddleOCR 识别验证码文字"""
        if img is None:
            return ""

        ocr = self.init_ocr()
        log.info("正在识别验证码 ...")

        # 转为 RGB（RGBA → RGB）
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # 保存临时文件供 PaddleOCR 读取
        tmp_path = "/tmp/_captcha.png"
        img.save(tmp_path)

        result = ocr.ocr(tmp_path)

        text = ""
        if result and result[0]:
            texts = [line[1][0] for line in result[0]]
            text = "".join(texts)
            log.info("OCR 识别结果: %s (置信度: %.2f)",
                      text, result[0][0][1][1] if result[0] else 0)

        # 清理临时文件
        try:
            os.remove(tmp_path)
        except OSError:
            pass

        return text.strip()

    # --------------------------------------------------
    # 填写并提交表单
    # --------------------------------------------------
    def submit_login(self, page, captcha_text: str):
        """填写表单并提交"""
        cfg = self.config
        form_data = {}

        # 获取已有表单字段
        # 用户名
        username_el = page.css(cfg["username_selector"])
        username_name = username_el[0].attrib.get("name", "") if username_el else ""
        if username_name:
            form_data[username_name] = cfg["username"]
        else:
            log.warning("未找到用户名输入框: %s", cfg["username_selector"])
            return None

        # 密码
        password_el = page.css(cfg["password_selector"])
        password_name = password_el[0].attrib.get("name", "") if password_el else ""
        if password_name:
            form_data[password_name] = cfg["password"]

        # 验证码
        captcha_el = page.css(cfg["captcha_input_selector"])
        captcha_name = captcha_el[0].attrib.get("name", "") if captcha_el else ""
        if captcha_name:
            form_data[captcha_name] = captcha_text
        else:
            log.warning("未找到验证码输入框: %s", cfg["captcha_input_selector"])
            return None

        log.info("表单数据(隐藏密码): %s", {
            k: ("******" if "pass" in k.lower() else v)
            for k, v in form_data.items()
        })

        # 获取表单提交 URL（优先取表单 action）
        form_el = page.css("form")
        action = ""
        if form_el:
            action = form_el[0].attrib.get("action", "")

        submit_url = urljoin(cfg["url"], action) if action else cfg["url"]
        log.info("正在提交登录: %s", submit_url)

        resp = self.session.post(submit_url, data=form_data, timeout=cfg["timeout"])
        log.info("登录响应状态码: %s", resp.status)
        return resp

    # --------------------------------------------------
    # 验证登录结果
    # --------------------------------------------------
    def check_login_success(self, response) -> bool:
        """检查登录是否成功"""
        body_text = response.text.lower()
        for kw in self.config["success_keywords"]:
            if kw.lower() in body_text:
                log.info("登录成功! (关键词: %s)", kw)
                return True

        # 检查是否有常见错误提示
        error_keywords = ["验证码错误", "验证码不正确", "验证码已过期",
                          "用户名或密码错误", "登录失败"]
        for kw in error_keywords:
            if kw.lower() in body_text:
                log.warning("登录失败，页面包含: %s", kw)
                return False

        # 检查 URL 是否变化（登录成功通常会跳转）
        log.warning("无法确定登录状态，请手动检查 response.text")
        return False

    # --------------------------------------------------
    # 主流程
    # --------------------------------------------------
    def run(self) -> bool:
        """执行完整登录流程，返回是否成功"""
        cfg = self.config
        self.init_session()

        for attempt in range(1, cfg["max_retries"] + 1):
            log.info("=" * 50)
            log.info("第 %d/%d 次尝试", attempt, cfg["max_retries"])
            log.info("=" * 50)

            try:
                # 1. 获取登录页面
                resp = self.fetch_login_page()

                # 2. 提取验证码图片
                img = self.extract_captcha_image(resp)
                if img is None:
                    log.warning("无法获取验证码图片，重试 ...")
                    continue

                # 3. 识别验证码
                captcha_text = self.recognize_captcha(img)
                if not captcha_text:
                    log.warning("验证码识别结果为空，重试 ...")
                    continue

                # 4. 提交登录
                login_resp = self.submit_login(resp, captcha_text)
                if login_resp is None:
                    continue

                # 5. 验证结果
                if self.check_login_success(login_resp):
                    return True

            except Exception as e:
                log.error("尝试失败: %s", e)

            if attempt < cfg["max_retries"]:
                log.info("等待 3 秒后重试 ...")
                time.sleep(3)

        log.error("已达最大重试次数，登录失败")
        return False


# ============================================================
# 简易入口
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("      Scrapling + PaddleOCR 自动登录工具")
    print("=" * 60)
    print()
    print("使用前请修改脚本顶部 CONFIG 配置")
    print()

    bot = LoginBot(CONFIG)
    success = bot.run()

    if success:
        print("\n✅ 登录成功!")
    else:
        print("\n❌ 登录失败，请检查配置或验证码识别")