#!/bin/bash
# 首次运行：python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
# （勿用 3.14 建 venv；勿从其他路径拷贝 .venv，否则 pip 会 bad interpreter）
# 之后：./run.sh 或 .venv/bin/python main.py（不要用 sudo）
[ "$(id -u)" = "0" ] && echo "请不要使用 sudo，直接执行: ./run.sh" && exit 1
cd "$(dirname "$0")"
if [ -d ".venv" ]; then
  .venv/bin/pip install setuptools -q 2>/dev/null || true
  # 闲鱼网页通道：需 playwright 包 + Chromium；install 已存在时几乎秒完成
  if .venv/bin/python -c "import playwright" 2>/dev/null; then
    echo "[xianyu-good] 检查 Playwright Chromium（网页任务需要）…"
    .venv/bin/python -m playwright install chromium || echo "[xianyu-good] 警告: 请手动执行: .venv/bin/python -m playwright install chromium"
  fi
  export VIRTUAL_ENV="${PWD}/.venv"
  PYVER=$(.venv/bin/python -c "import sys; print('%s.%s' % (sys.version_info.major, sys.version_info.minor))" 2>/dev/null)
  SITE=""
  [ -n "$PYVER" ] && [ -d ".venv/lib/python$PYVER/site-packages" ] && SITE="${PWD}/.venv/lib/python${PYVER}/site-packages"
  [ -n "$SITE" ] && export PYTHONPATH="${SITE}:${PYTHONPATH}"
  exec .venv/bin/python main.py
else
  exec python3 main.py
fi
