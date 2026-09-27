#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -x .venv/bin/python ]; then
    if ! "$PYTHON_BIN" -m venv .venv; then
        printf '%s\n' '无法创建虚拟环境。Ubuntu/Debian 请安装 python3-venv，再重新执行本脚本。' >&2
        printf '%s\n' '也可以将 PYTHON_BIN 指向带 venv 支持的 Python 3.12+。' >&2
        exit 1
    fi
fi
.venv/bin/python -m pip install -r requirements.lock
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
fi
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createcachetable
.venv/bin/python manage.py seed_routes
printf '%s\n' '准备完成。打开 http://127.0.0.1:8000；按 Ctrl+C 停止。'
exec .venv/bin/python manage.py runserver 127.0.0.1:8000
