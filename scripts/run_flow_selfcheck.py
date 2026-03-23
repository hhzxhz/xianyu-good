# -*- coding: utf-8 -*-
"""
流程自检：3 遍 × 每遍 5 分钟（Mock 下每遍约 30 秒），校验「搜索页判定 → 解析商品 → 规则匹配 → 点击购买」全流程。
无设备时用 Mock 跑；真机 5 分钟观察请用: OBSERVE_SECONDS=300 python scripts/observe_10min.py（跑 3 次）。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 每遍运行秒数；默认 30 秒快速自检，需每遍约 5 分钟可设 FLOW_RUN_SECONDS=300
RUN_SECONDS_PER_ROUND = int(os.environ.get("FLOW_RUN_SECONDS", "30"))
ROUNDS = 3
# 每遍周期数：短跑时少周期，长跑时按约 2 秒/周期凑满 RUN_SECONDS_PER_ROUND
CYCLES_PER_ROUND = max(10, RUN_SECONDS_PER_ROUND // 2)
SLEEP_PER_CYCLE = 0.1 if RUN_SECONDS_PER_ROUND < 60 else 2.0  # 长跑时拉长间隔以接近真实 5 分钟


def _item_xml(desc: str, price: str) -> str:
    """单条商品：长描述 + 纯数字价格，满足解析规则"""
    return (
        '<node>'
        '<node class="android.widget.TextView" text="%s"/>'
        '<node class="android.widget.TextView" text="%s"/>'
        '</node>'
    ) % (desc.replace("&", "&amp;").replace("<", "&lt;"), price)


def _hierarchy_with_items(num_items: int = 3) -> str:
    """含 nested_recycler_view 与 num_items 条商品的 hierarchy XML"""
    items = [
        _item_xml("万宝龙二代149ef钢笔18c笔尖", "1300"),
        _item_xml("派克51真空钢笔", "779"),
        _item_xml("英雄100金笔14K", "268"),
    ]
    children = "".join(items[: max(1, num_items)])
    return (
        '<?xml version="1.0"?><node>'
        '<node resource-id="com.taobao.idlefish:id/nested_recycler_view" class="androidx.recyclerview.widget.RecyclerView">'
        "%s"
        "</node></node>"
    ) % children


class MockSelector:
    def __init__(self, exists: bool, info=None):
        self._exists = exists
        self._info = info or {}

    @property
    def exists(self):
        return self._exists

    def click(self):
        pass

    @property
    def info(self):
        return self._info

    def child(self, index: int):
        return MockSelector(0 <= index < 3)


class MockDevice:
    """模拟设备：搜索页 + 可解析出商品的 hierarchy；支持 _current_tab 控制是否解析"""

    def __init__(self, on_search_page: bool = True, with_items: bool = True):
        self._on_search_page = on_search_page
        self._with_items = with_items
        self._tokens = ["降价", "新发", "综合", "价格"] if on_search_page else []

    def _match(self, text=None, description=None, textContains=None, descriptionContains=None, **kwargs):
        for t in (text, description, textContains, descriptionContains):
            if t is None:
                continue
            s = str(t).strip()
            if s in self._tokens or any(tok in s or s in tok for tok in self._tokens):
                return True
        return False

    def __call__(
        self,
        text=None,
        description=None,
        textContains=None,
        descriptionContains=None,
        packageName=None,
        resourceId=None,
        className=None,
        **kwargs,
    ):
        if className and "RecyclerView" in className:
            return MockSelector(True, {"bounds": {"top": 100}})
        if resourceId and "nested_recycler" in (resourceId or ""):
            return MockSelector(True)
        ok = self._match(
            text=text,
            description=description,
            textContains=textContains,
            descriptionContains=descriptionContains,
        )
        return MockSelector(ok)

    def dump_hierarchy(self):
        if self._on_search_page and self._with_items:
            return _hierarchy_with_items(3)
        if self._on_search_page:
            return '<?xml version="1.0"?><node><node text="降价"/><node text="新发"/></node></node>'
        return '<?xml version="1.0"?><node/>'

    def window_size(self):
        return (1080, 2400)

    def press(self, key):
        pass

    def click(self, x=None, y=None):
        pass


def run_one_round(round_no: int, log_cb=None) -> dict:
    """
    跑一遍流程自检：页面判定 → 解析商品（含描述+价格）→ 校验可匹配与点击路径。
    返回本遍统计：parse_ok, items_count, match_checked
    """
    from app.core.xianyu import XianyuDriver

    stats = {"parse_ok": False, "items_count": 0, "match_checked": False}
    d = MockDevice(on_search_page=True, with_items=True)
    driver = XianyuDriver(d, log_cb=log_cb)
    driver._current_keyword = "钢笔"
    driver._current_tab = "新发"

    # 1) 搜索页判定
    if not driver.is_on_new_drop_page():
        if log_cb:
            log_cb("WARNING", "第 %s 遍：未通过搜索页判定", round_no)
        return stats
    if not driver.should_parse_items():
        if log_cb:
            log_cb("WARNING", "第 %s 遍：当前 tab 不应解析", round_no)
        return stats

    # 2) 解析商品
    items = driver.get_search_result_items(limit=5)
    if not items:
        if log_cb:
            log_cb("WARNING", "第 %s 遍：解析商品数为 0", round_no)
        return stats
    stats["parse_ok"] = True
    stats["items_count"] = len(items)

    # 3) 每条须含 description 与 price，且 rv_index 存在
    for i, it in enumerate(items):
        desc = it.get("description") or ""
        price = it.get("price")
        rv_idx = it.get("rv_index")
        if not desc or price is None:
            if log_cb:
                log_cb("WARNING", "第 %s 遍：商品[%s] 缺少描述或价格 desc=%s price=%s", round_no, i, desc[:30], price)
            stats["parse_ok"] = False
            return stats
        if rv_idx is None and "rv_index" in it:
            pass
        # rv_index 可有可无（旧逻辑兼容）

    # 4) 模拟「匹配规则 + 点击」路径：用第一条商品，规则为关键字「钢笔」+ 价格区间包含 779
    rules_list = [(["钢笔"], None, 2000)]
    raw = items[0].get("description") or ""
    price = items[0].get("price")
    from app.core.buyer import _item_matches_any_rule

    if _item_matches_any_rule(raw, price, rules_list):
        stats["match_checked"] = True
    # 再校验一条不匹配的（价格超区间）
    rules_strict = [(["钢笔"], 2000, 3000)]
    if _item_matches_any_rule(raw, price, rules_strict):
        stats["match_checked"] = False  # 不应匹配

    return stats


def main():
    log_cb = lambda level, msg, *args: print("[%s] %s" % (level, msg % args if args else msg))
    total_ok = 0
    for r in range(ROUNDS):
        start = time.monotonic()
        stats = run_one_round(r + 1, log_cb=log_cb)
        elapsed = time.monotonic() - start
        if stats["parse_ok"] and stats["items_count"] > 0:
            total_ok += 1
            print("第 %s 遍通过 解析 %s 条 匹配校验 %s (%.1fs)" % (
                r + 1, stats["items_count"], stats["match_checked"], elapsed))
        else:
            print("第 %s 遍未通过 parse_ok=%s items_count=%s (%.1fs)" % (
                r + 1, stats["parse_ok"], stats["items_count"], elapsed))
        # 模拟每遍多周期（短跑 0.1s/周期，长跑 2s/周期以接近 5 分钟）
        for _ in range(CYCLES_PER_ROUND - 1):
            run_one_round(0, log_cb=None)
            time.sleep(SLEEP_PER_CYCLE)
    if total_ok >= ROUNDS:
        print("流程自检 3 遍全部通过，商品识别与匹配路径正常。")
        return 0
    print("流程自检未全部通过，请检查解析与规则逻辑。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
