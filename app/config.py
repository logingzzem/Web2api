"""应用配置，从 .env 读取。"""

import os
import tempfile
from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，从环境变量或 .env 文件读取。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 会员网站 URL
    login_url: str = Field(default="https://example.com/login", description="登录页 URL")
    landing_url: str = Field(
        default="https://example.com/",
        description="登录后的落地页 URL（从这里开始点击导航按钮）",
    )
    form_url: str = Field(
        default="", description="直接访问的表单页 URL（留空则走 navigation_steps）"
    )

    # 登录账号
    member_username: str = Field(default="", description="会员账号")
    member_password: str = Field(default="", description="会员密码")

    # 登录表单字段选择器（默认常见选择器，多个值用英文逗号分隔，按顺序尝试）
    username_selector: str = Field(
        default="input[name='username'],#username,input[type='text']",
        description="用户名输入框选择器",
    )
    password_selector: str = Field(
        default="input[name='password'],#password,input[type='password']",
        description="密码输入框选择器",
    )
    login_button_selector: str = Field(
        default="button[type='submit'],input[type='submit'],.login-btn",
        description="登录按钮选择器",
    )

    # ---- 新增：登录后的导航步骤 ----
    # 用英文逗号分隔的按钮文字列表，Agent 会按顺序查找并点击
    # 例如："会员管理,添加,新增"  →  先找"会员管理"并点击，再找"添加"并点击
    navigation_steps: str = Field(
        default="",
        description="登录后到表单前需依次点击的按钮文字（逗号分隔，如：会员管理,添加,新增）",
    )

    # 提交按钮候选文字（逗号分隔，多候选，按顺序尝试）
    submit_button_texts: str = Field(
        default="提交,保存,确认,Submit,Save,Confirm",
        description="提交按钮候选文字（逗号分隔，找不到 CSS 选择器时按文字查找）",
    )

    # 提交按钮 CSS 选择器（逗号分隔，优先顺序）
    submit_button_selector: str = Field(
        default="button[type='submit'],input[type='submit']", description="表单提交按钮选择器"
    )

    # 表单加载完成的标志（等待该元素可见才算表单打开）
    form_ready_selector: str = Field(
        default="", description="表单加载完成标志选择器（如 .modal-dialog，留空则不等待）"
    )

    # 浏览器配置
    browser_headless: bool = Field(default=True, description="是否使用无头模式")
    browser_timeout_ms: int = Field(default=30000, description="浏览器操作超时时间（毫秒）")

    # 截图保存目录
    screenshot_dir: str = Field(default="", description="截图保存目录，留空则使用系统临时目录")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 如果 screenshot_dir 为空，使用系统临时目录下的 web2api 子目录
        if not self.screenshot_dir:
            self.screenshot_dir = os.path.join(tempfile.gettempdir(), "web2api")

    def get_field_selector(self, field_name: str) -> str | None:
        """根据 API 字段名获取对应的页面选择器。

        读取环境变量 FORM_FIELD_<FIELD_NAME_UPPER>。
        例如 field_name="member_name" → 环境变量 FORM_FIELD_MEMBER_NAME
        """
        env_key = f"FORM_FIELD_{field_name.upper()}"
        return os.environ.get(env_key)

    def get_all_field_selectors(self) -> dict[str, str]:
        """获取所有已配置字段的映射字典。"""
        result: dict[str, str] = {}
        for key, value in os.environ.items():
            if key.startswith("FORM_FIELD_"):
                api_field = key[len("FORM_FIELD_") :].lower()
                result[api_field] = value
        return result

    def get_navigation_steps(self) -> list[str]:
        """解析 navigation_steps 为有序列表（忽略空项与空格）。"""
        if not self.navigation_steps:
            return []
        steps = [s.strip() for s in self.navigation_steps.split(",") if s.strip()]
        return steps

    def get_submit_button_texts(self) -> list[str]:
        """解析提交按钮候选文字为列表。"""
        if not self.submit_button_texts:
            return []
        return [s.strip() for s in self.submit_button_texts.split(",") if s.strip()]

    def use_navigation_mode(self) -> bool:
        """判断是否启用『点击导航按钮』模式（非直接 goto form_url）。"""
        return bool(self.get_navigation_steps())

    @staticmethod
    def split_selectors(raw: str) -> list[str]:
        """把逗号分隔的多个选择器拆成列表。"""
        if not raw:
            return []
        return [s.strip() for s in raw.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    """获取单例配置实例。"""
    return Settings()
