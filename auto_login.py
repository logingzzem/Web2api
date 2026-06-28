"""
基于 Scrapling + PaddleOCR 的自动登录程序
支持图形验证码识别

用法：
    1. 修改下面的 CONFIG 配置
    2. python auto_login.py

依赖：
    pip install scrapling paddleocr opencv-python-headless Pillow
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
from scrapling.fetchers import FetcherSession

import cv2
import numpy as np

# PaddleOCR 懒加载
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
    "url": "https://scrm.asphel.cn/MredLNqPiT.php/index/login?name=mes",
    "username": "mes",
    "password": "123456",

    # -------- 表单字段的 name 属性 --------
    # 如果不确定，留空让程序从页面自动检测
    "field_username": "username",
    "field_password": "password",
    "field_captcha": "captcha",
    "field_token": "__token__",           # CSRF Token 的 name

    # -------- 验证码图片地址（CSS 选择器 / 固定 URL）-------
    # 可以是:
    #   - CSS 选择器字符串，如 "#captcha-img"
    #   - 完整 URL 字符串
    #   - 设为 None 则从 <img> 标签自动提取
    "captcha_selector": None,
    # 验证码图片 URL（如果有固定地址）
    "captcha_url": "/index.php?s=/captcha",

    # -------- 提交方式 --------
    # form_action: None 表示提交到当前页面 URL
    # form_selector: 表单的 CSS 选择器，用于提取 CSRF Token
    "form_selector": "form",
    "form_action": None,

    # -------- 额外表单字段 --------
    "extra_fields": {"keeplogin": "1"},

    # -------- 重试设置 --------
    "max_retries": 30,
    "retry_delay": 1,
    "timeout": 30,

    # -------- OCR 置信度阈值 --------
    # 低于此值的4位结果不提交，刷新验证码重试
    # 设为 0 则接受所有结果（最激进），设为 0.9 则只接受高置信度（最保守）
    "min_confidence": 0.0,

    # -------- 登录成功判断 --------
    "success_keywords": ["登录成功", "欢迎", "dashboard", "退出"],
    "success_title_regex": r"成功",
}


class LoginBot:
    """基于 Scrapling + PaddleOCR 的自动登录机器人"""

    def __init__(self, config: dict):
        self.config = config

    # --------------------------------------------------
    # 图片预处理
    # --------------------------------------------------
    @staticmethod
    def preprocess_captcha(img: Image.Image) -> Image.Image:
        """对验证码图片进行预处理，提高 OCR 准确率"""
        import cv2
        import numpy as np

        img = img.convert("RGB")
        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)

        # 中值滤波去除椒盐噪点
        cv_img = cv2.medianBlur(cv_img, 3)

        # 对比度增强
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
        cv_img = clahe.apply(cv_img)

        # 二值化（OTSU 自动阈值）
        _, cv_img = cv2.threshold(cv_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 形态学操作：去除细小干扰线
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        cv_img = cv2.morphologyEx(cv_img, cv2.MORPH_CLOSE, kernel)

        return Image.fromarray(cv_img)

    # --------------------------------------------------
    # OCR 识别验证码
    # --------------------------------------------------
    def recognize_captcha(self, img: Image.Image) -> str:
        """识别验证码：多策略predict + 4位严格筛选"""
        import cv2
        import numpy as np
        import re

        ocr = _get_ocr()
        candidates = []  # [(text, score, label)]

        def run_predict(nd_img, label):
            tmp = "/tmp/_c_pred.png"
            cv2.imwrite(tmp, nd_img)
            result = ocr.predict(tmp)
            if not result or not isinstance(result, list) or len(result) == 0:
                return
            texts = result[0].get("rec_texts", [])
            scores = result[0].get("rec_scores", [])
            if texts:
                merged = "".join(texts)
                clean = re.sub(r"[^A-Za-z0-9]", "", merged)
                if clean:
                    candidates.append((clean, sum(scores)/len(scores), f"{label}_pred"))
            for t, s in zip(texts, scores):
                clean = re.sub(r"[^A-Za-z0-9]", "", t)
                if clean and s >= 0.3:
                    candidates.append((clean, s, f"{label}_pred_seg"))

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        rgb = np.array(img.convert("RGB"))

        # 策略1: 原始图
        run_predict(rgb, "raw")

        # 策略2: CLAHE + OTSU + 连通区域去噪
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        gray = cv2.medianBlur(gray, 3)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        gray = clahe.apply(gray)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        filtered = np.zeros_like(binary)
        for i in range(1, n_labels):
            if stats[i, cv2.CC_STAT_AREA] >= 100 and stats[i, cv2.CC_STAT_HEIGHT] >= 12:
                filtered[labels == i] = 255
        run_predict(filtered, "clean")

        # 筛选4位纯字母数字提交
        min_conf = self.config.get("min_confidence", 0.0)
        good = []
        low_conf = []
        for t, s, lbl in candidates:
            if len(t) == 4 and re.fullmatch(r"[A-Za-z0-9]{4}", t):
                if s >= min_conf:
                    good.append((t.upper(), s, lbl))
                else:
                    low_conf.append((t.upper(), s, lbl))

        if good:
            best = max(good, key=lambda x: x[1])
            log.info("OCR 4位: %s (置信度: %.4f, 来源: %s)", best[0], best[1], best[2])
            return best[0]

        if low_conf:
            max_c = max(s for _, s in low_conf)
            log.info("OCR 置信度不足(最高%.4f < %.2f)，刷新验证码重试", max_c, min_conf)
            return ""

        log.info("OCR 未识别出有效4位结果，跳过本次")
        return ""

    # --------------------------------------------------
    # 获取页面 & 下载验证码
    # --------------------------------------------------
    def fetch_page_and_captcha(self, session):
        """获取登录页面 HTML，提取 CSRF Token，下载验证码图片"""
        cfg = self.config
        url = cfg["url"]

        # 获取页面
        resp = session.get(url, timeout=cfg["timeout"])
        html = resp.body.decode("utf-8")

        # 提取 CSRF Token
        token = ""
        token_field = cfg.get("field_token", "__token__")
        m = re.search(
            rf'name="{re.escape(token_field)}"\s+value="([^"]*)"',
            html,
        )
        if m:
            token = m.group(1)

        # 下载验证码图片
        captcha_url = cfg.get("captcha_url")
        if captcha_url:
            if not captcha_url.startswith("http"):
                captcha_url = urljoin(url, captcha_url)
        else:
            sel = cfg.get("captcha_selector")
            if sel:
                m = re.search(rf'<img[^>]+src="([^"]+)"', html)
                captcha_url = m.group(1) if m else ""
                if captcha_url and not captcha_url.startswith("http"):
                    captcha_url = urljoin(url, captcha_url)

        img = None
        if captcha_url:
            img_resp = session.get(captcha_url, timeout=cfg["timeout"])
            if img_resp.status == 200 and len(img_resp.body) > 0:
                img = Image.open(BytesIO(img_resp.body))

        return html, token, img

    # --------------------------------------------------
    # 提交登录
    # --------------------------------------------------
    def submit_login(self, session, html: str, token: str, captcha_text: str):
        """提交登录表单"""
        cfg = self.config
        url = cfg["url"]

        form_data = {}

        # CSRF Token
        if token:
            form_data[cfg.get("field_token", "__token__")] = token

        # 用户名 / 密码 / 验证码
        form_data[cfg.get("field_username", "username")] = cfg["username"]
        form_data[cfg.get("field_password", "password")] = cfg["password"]
        form_data[cfg.get("field_captcha", "captcha")] = captcha_text

        # 额外字段
        form_data.update(cfg.get("extra_fields", {}))

        log.info("提交数据 (密码隐藏): %s", {
            k: ("******" if "pass" in k.lower() else v)
            for k, v in form_data.items()
        })

        # 提交
        action = cfg.get("form_action")
        submit_url = urljoin(url, action) if action else url
        resp = session.post(submit_url, data=form_data, timeout=cfg["timeout"])
        return resp

    # --------------------------------------------------
    # 验证登录结果
    # --------------------------------------------------
    def check_success(self, resp_body: str) -> bool:
        """检查登录是否成功"""
        cfg = self.config

        # 检查成功关键词
        body_lower = resp_body.lower()
        for kw in cfg["success_keywords"]:
            if kw.lower() in body_lower:
                log.info("登录成功! (关键词: %s)", kw)
                return True

        # 检查标题
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", resp_body, re.DOTALL)
        title_regex = cfg.get("success_title_regex")
        if title_regex and h1:
            if re.search(title_regex, h1.group(1)):
                log.info("登录成功! (标题匹配: %s)", h1.group(1).strip())
                return True

        # 检查是否还在登录页
        login_indicators = ["验证码错误", "验证码不正确", "密码错误", "用户名不存在"]
        for kw in login_indicators:
            if kw in resp_body:
                log.warning("登录失败: %s", kw)
                return False

        log.warning("无法确定登录状态")
        return False

    # --------------------------------------------------
    # 主流程
    # --------------------------------------------------
    def run(self, round_num: int = 1) -> tuple:
        """执行完整登录流程，返回 (是否成功, 截图路径)"""
        cfg = self.config

        for attempt in range(1, cfg["max_retries"] + 1):
            log.info("--- 第 %d/%d 次尝试 ---", attempt, cfg["max_retries"])

            try:
                with FetcherSession() as session:
                    # 1. 获取页面 + 下载验证码
                    html, token, img = self.fetch_page_and_captcha(session)
                    if img is None:
                        log.warning("验证码下载失败")
                        continue

                    # 2. 识别验证码
                    captcha_text = self.recognize_captcha(img)
                    if not captcha_text:
                        log.warning("验证码识别为空")
                        continue

                    # 3. 提交登录
                    resp = self.submit_login(session, html, token, captcha_text)
                    resp_body = resp.body.decode("utf-8")

                    # 4. 保存验证码截图（保留供查看，文件名含轮次+时间戳）
                    import time as _time
                    ts = _time.strftime("%H%M%S")
                    screenshot_path = f"/workspace/captcha_round{round_num}_{ts}.png"
                    img.save(screenshot_path)

                    # 5. 检查结果
                    if self.check_success(resp_body):
                        return True, screenshot_path

            except Exception as e:
                log.error("异常: %s", e)

            if attempt < cfg["max_retries"]:
                time.sleep(cfg.get("retry_delay", 1))

        log.error("已达最大重试次数，登录失败")
        return False, ""


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    for i in range(1, n + 1):
        print("=" * 60)
        print(f"  第 {i}/{n} 轮测试")
        print("=" * 60)
        bot = LoginBot(CONFIG)
        success, screenshot_path = bot.run(round_num=i)
        print(f"  结果: {'✅ 登录成功' if success else '❌ 登录失败'}")
        if screenshot_path:
            print(f"  验证码截图: {screenshot_path}")
        print()