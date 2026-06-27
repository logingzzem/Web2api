#!/bin/bash
# Scrapling + PaddleOCR 自动登录启动脚本
# 使用 Python 3.12 环境（PaddleOCR 需要）

PYTHON="/root/.pyenv/versions/3.12.13/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "错误: 未找到 Python 3.12 ($PYTHON)"
    echo "请检查 Python 3.12 安装路径"
    exit 1
fi

exec "$PYTHON" /workspace/auto_login.py "$@"