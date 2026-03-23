# -*- coding: utf-8 -*-
"""用你提供的入参结构执行 test.main 一次（extract_result 为纪要正文时从 hal_text 的 JSON 推断总条目数）"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from test import main

# 你提供的 hal_text 中的 JSON 块（含 总条目数 数组与 幻觉条目总数）
HAL_JSON_BLOCK = """```json
{
  "总条目数": [
    {"纪要内容": "会议共识：1. 指令生成逻辑...", "原文对照": "王彬 10:07...", "理由": "决策幻觉。"},
    {"纪要内容": "会议共识：5. 项目排期...", "原文对照": "会议日期：2026年3月5日...", "理由": "时间幻觉与决策幻觉。"},
    {"纪要内容": "□ 提测安排...", "原文对照": "王梦茹 15:44...", "理由": "时间幻觉与决策幻觉。"},
    {"纪要内容": "□ 测试执行...", "原文对照": "会议日期：2026年3月5日...", "理由": "时间幻觉。"},
    {"纪要内容": "□ PRD文档编写...", "原文对照": "吴红运 16:44...", "理由": "不在原文。"}
  ],
  "幻觉条目总数": 5
}
```"""

# extract_result 你给的是纪要正文（非 JSON），main 内会走 fallback 用 hal 的 总条目数
EXTRACT_RESULT = "提示词评测-【历史妙记支持指令生成英文纪要】\n评测信息\n? 妙记链接: https://mi.feishu.cn/minutes/obcn89814iy459f7837x7d82\n? 提示词名称: 会议纪要-总结\n? 使用模型: mimo-v2-flash\n生成内容\n总结\n会议目的：本次会议围绕历史妙记灵活支持指令生成英文纪要的需求评审展开。"

if __name__ == "__main__":
    result = main(HAL_JSON_BLOCK, EXTRACT_RESULT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
