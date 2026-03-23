# -*- coding: utf-8 -*-
"""用指定入参执行 test.main，入参从 JSON 文件读取"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from test import main

if __name__ == "__main__":
    input_path = ROOT / "test_input.json"
    if not input_path.exists():
        print("请创建 test_input.json，内容为 {\"extract_result\": \"...\", \"hal_text\": \"...\"}", file=sys.stderr)
        sys.exit(2)
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = main(data["hal_text"], data["extract_result"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
