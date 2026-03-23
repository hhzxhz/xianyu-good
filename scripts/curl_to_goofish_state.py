#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将「复制为 cURL」保存的文本转为仅含 cookies 的 Playwright storage_state JSON。
用法：python scripts/curl_to_goofish_state.py [输入.curl.txt] [输出.json]
默认：goofish.curl.txt -> 项目根 goofish_state.json
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.goofish_curl_cookies import load_storage_state_from_curl_file  # noqa: E402


def main() -> None:
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else _ROOT / "goofish.curl.txt"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else _ROOT / "goofish_state.json"
    if not inp.is_file():
        print("找不到文件: %s" % inp, file=sys.stderr)
        sys.exit(1)
    state = load_storage_state_from_curl_file(inp)
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入 %s（共 %s 条 Cookie）" % (out, len(state.get("cookies") or [])))


if __name__ == "__main__":
    main()
