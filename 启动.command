#!/bin/bash
# 文科论文写作助手 - macOS 启动脚本
# 首次使用：chmod +x 启动.command

cd "$(dirname "$0")"

echo "================================================"
echo "  文科论文写作助手 - 启动"
echo "================================================"

# 1. 找 Python 3.11+
PY=""
for cand in python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver=$("$cand" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if [ "$(echo "$ver" | awk -F. '{print ($1>3)||($1==3&&$2>=11)}')" = "1" ]; then
      PY="$cand"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "[错误] 未找到 Python 3.11+。请安装 https://www.python.org/downloads/"
  read -r -p "按回车退出…"
  exit 1
fi
echo "[1/3] Python: $PY ($($PY --version 2>&1))"

# 2. 检查依赖
if ! "$PY" -c "import fastapi, uvicorn, httpx, fitz, docx, numpy, sqlite_vec" >/dev/null 2>&1; then
  echo "[2/3] 依赖缺失，正在安装（首次需要联网）..."
  "$PY" -m pip install -r requirements.txt
  if [ $? -ne 0 ]; then
    echo "[错误] 依赖安装失败，请检查网络后重试"
    read -r -p "按回车退出…"
    exit 1
  fi
else
  echo "[2/3] 依赖已就绪"
fi

# 3. 找端口并启动
PORT=8000
while [ $PORT -le 8010 ]; do
  if ! lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  PORT=$((PORT+1))
done
if [ $PORT -gt 8010 ]; then
  echo "[错误] 端口 8000-8010 全部被占用"
  read -r -p "按回车退出…"
  exit 1
fi
echo "[3/3] 启动后端： http://127.0.0.1:$PORT"
echo "      关闭本窗口即停止程序（数据已保存在 data/ 目录）"
sleep 1
open "http://127.0.0.1:$PORT"
PORT=$PORT "$PY" -X utf8 -m backend.main

echo
echo "程序已停止。"
read -r -p "按回车退出…"
