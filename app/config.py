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
    form_url: str = Field(default="https://example.com/form/upload", description="表单上传页 URL")

    # 登录账号
    member_username: str = Field(default="", description="会员账号")
    member_password: str = Field(default="", description="会员密码")

    # 登录表单字段选择器（默认常见选择器）
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

    # 提交按钮选择器
    submit_button_selector: str = Field(
        default="button[type='submit'],input[type='submit']", description="表单提交按钮选择器"
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


@lru_cache
def get_settings() -> Settings:
    """获取单例配置实例。"""
    return Settings()
