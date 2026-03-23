import json
import re

def main(deepseek_hal_text: str, Qwen3_235B_hal_text: str, mimo: str, recall_text: str) -> dict:
    """
    合并 deepseek / Qwen3_235B / mimo 的幻觉与召回结果，输出统一结构。
    :param deepseek_hal_text: deepseek 幻觉评测 JSON 或 ```json ... ``` 包裹文本
    :param Qwen3_235B_hal_text: Qwen3_235B 幻觉评测文本
    :param mimo: mimo 幻觉评测文本（调用方关键字为 mimo，与形参一致）
    :param recall_text: 召回结果 JSON 字符串
    :return: {"result": 合并后的 JSON 字符串}
    """
    def parse_hal(hal_text: str):
        if hal_text is None or not str(hal_text).strip():
            raise ValueError("hal 文本为空")
        m = re.search(r'```json\s*(\{.*?\})\s*```', hal_text, flags=re.S)
        s = m.group(1).strip() if m else hal_text.strip()
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError("hal 文本不是合法 JSON: %s" % e) from e

    deepseek_hal = parse_hal(deepseek_hal_text)
    Qwen3_235B_hal = parse_hal(Qwen3_235B_hal_text)
    mimo_hal = parse_hal(mimo)
    if recall_text is None or not str(recall_text).strip():
        raise ValueError("recall_text 为空")
    try:
        recall_data = json.loads(recall_text.strip())
    except json.JSONDecodeError as e:
        raise ValueError("recall_text 不是合法 JSON: %s" % e) from e

    categories = ["核心议题", "关键决策", "待办任务", "其他信息"]
    deepseek_cat_recall = {}
    Qwen3_235B_cat_recall = {}
    mimo_cat_recall = {}
    cat_recall = recall_data.get("分类结果")
    if isinstance(cat_recall, dict):
        for c in categories:
            cr = cat_recall.get(c, {})
            cat_total = int(cr.get("合并非幻觉条目总数", len(cr.get("合并非幻觉数据", []) or [])))
            deepseek_cat_recall[c] = {"合并非幻觉条目总数": cat_total, **(cr.get("deepseek") or {})}
            Qwen3_235B_cat_recall[c] = {"合并非幻觉条目总数": cat_total, **(cr.get("Qwen3_235B") or {})}
            mimo_cat_recall[c] = {"合并非幻觉条目总数": cat_total, **(cr.get("mimo") or {})}

    out_dict = {
        "合并非幻觉条目总数": recall_data["合并非幻觉条目总数"],
        # ↓↓↓ 仅新增这一行，消除「18 vs 13」误解
        "原始总条目数": {"deepseek": deepseek_hal["总条目数"], "Qwen3_235B": Qwen3_235B_hal["总条目数"], "mimo": mimo_hal["总条目数"]},
        "deepseek": {
            "幻觉率": deepseek_hal["幻觉率"],
            "幻觉条目总数": deepseek_hal["幻觉条目总数"],
            "总条目数": deepseek_hal["总条目数"],
            "召回率": recall_data["deepseek"]["召回率"],
            "合并后条目数": recall_data["deepseek"]["合并后条目数"],
            "分类数据": deepseek_hal.get("分类数据", {}),
            "分类召回": deepseek_cat_recall
        },
        "Qwen3_235B": {
            "幻觉率": Qwen3_235B_hal["幻觉率"],
            "幻觉条目总数": Qwen3_235B_hal["幻觉条目总数"],
            "总条目数": Qwen3_235B_hal["总条目数"],
            "召回率": recall_data["Qwen3_235B"]["召回率"],
            "合并后条目数": recall_data["Qwen3_235B"]["合并后条目数"],
            "分类数据": Qwen3_235B_hal.get("分类数据", {}),
            "分类召回": Qwen3_235B_cat_recall
        },
        "mimo": {
            "幻觉率": mimo_hal["幻觉率"],
            "幻觉条目总数": mimo_hal["幻觉条目总数"],
            "总条目数": mimo_hal["总条目数"],
            "召回率": recall_data.get("mimo", {}).get("召回率"),
            "合并后条目数": recall_data.get("mimo", {}).get("合并后条目数"),
            "分类数据": mimo_hal.get("分类数据", {}),
            "分类召回": mimo_cat_recall
        }
    }
    return {"result": json.dumps(out_dict, ensure_ascii=False)}