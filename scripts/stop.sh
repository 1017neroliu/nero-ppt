#!/bin/sh
# 停止 Nero PPT 的 Web 与 Worker（按 pid 文件，回退按进程名匹配）
DIR="${1:-$(dirname "$0")/..}"
cd "$DIR"
for name in web worker; do
  pidfile="logs/$name.pid"
  if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "[stop] $name($pid) 已停止"
    fi
    rm -f "$pidfile"
  fi
done
pkill -f "nero_ppt.cli worker" 2>/dev/null && echo "[stop] 兜底清理 worker" || true
exit 0
