#!/usr/bin/env bash
# 本地预览：读取 dev.env（首次运行自动生成随机口令），用演示库启动。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f dev.env ]; then
  printf 'ADMIN_PASSWORD=%s\nWEBHOOK_SECRET=%s\nCHUHAI_DB=data/demo.db\nSITE_URL=http://127.0.0.1:8090\n' \
    "$(openssl rand -hex 8)" "$(openssl rand -hex 16)" > dev.env
fi
set -a; . ./dev.env; set +a
[ -f "$CHUHAI_DB" ] || .venv/bin/python scripts/seed_demo.py
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090 --reload --reload-dir app
