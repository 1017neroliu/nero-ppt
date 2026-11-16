#!/bin/sh
# Nero PPT 启动脚本：Web(8630) + Worker 两个进程
# 用法: scripts/start.sh [工作目录，默认项目根]
set -e
DIR="${1:-$(dirname "$0")/..}"
cd "$DIR"
mkdir -p data logs
echo "[start] Web → http://127.0.0.1:8630 (日志 logs/web.log)"
nohup python3 -m nero_ppt.cli serve --host 127.0.0.1 --port 8630 > logs/web.log 2>&1 &
echo $! > logs/web.pid
echo "[start] Worker → logs/worker.log"
nohup python3 -m nero_ppt.cli worker > logs/worker.log 2>&1 &
echo $! > logs/worker.pid
sleep 1
echo "[start] 完成。停止：scripts/stop.sh"
