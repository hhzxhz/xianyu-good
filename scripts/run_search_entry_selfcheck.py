# -*- coding: utf-8 -*-
"""
进入搜索页逻辑自检：无设备时用 Mock 跑 3 遍，验证无报错。
需真机验证时请执行: OBSERVE_SECONDS=30 .venv/bin/python scripts/observe_10min.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


class MockSelector:
    def __init__(self, exists: bool):
        self._exists = exists

    @property
    def exists(self):
        return self._exists

    def click(self):
        pass

    @property
    def info(self):
        return {}


class MockDevice:
    """模拟设备：dump 返回含「降价」「新发」的 XML 时视为在搜索页"""

    def __init__(self, on_search_page: bool = True):
        self._on_search_page = on_search_page
        self._tokens = ["降价", "新发", "综合", "价格"] if on_search_page else []

    def _match(self, text=None, description=None, textContains=None, descriptionContains=None):
        for t in (text, description, textContains, descriptionContains):
            if t is None:
                continue
            s = str(t).strip()
            if s in self._tokens or any(tok in s or s in tok for tok in self._tokens):
                return True
        return False

    def __call__(self, text=None, description=None, textContains=None, descriptionContains=None, **kwargs):
        ok = self._match(text=text, description=description, textContains=textContains, descriptionContains=descriptionContains)
        return MockSelector(ok)

    def dump_hierarchy(self):
        if self._on_search_page:
            return '<?xml version="1.0"?><node><node text="降价"/><node text="新发"/></node></node>'
        return "<?xml version=\"1.0\"?><node/>"

    def window_size(self):
        return (1080, 2400)

    def press(self, key):
        pass

    def click(self, x=None, y=None):
        pass


def run_one(log_cb=None):
    """跑一遍：用 Mock 设备只做「在搜索页」判定与 _ensure_on_search_list（在搜索页分支），不触发填词/首页等需真机逻辑"""
    from app.core.xianyu import XianyuDriver

    d = MockDevice(on_search_page=True)
    driver = XianyuDriver(d, log_cb=log_cb)
    driver._current_keyword = "钢笔"
    assert driver.is_on_new_drop_page() is True
    assert driver._ensure_on_search_list() is True

    d2 = MockDevice(on_search_page=False)
    driver2 = XianyuDriver(d2, log_cb=log_cb)
    driver2._current_keyword = "钢笔"
    assert driver2.is_on_new_drop_page() is False
    return True


if __name__ == "__main__":
    try:
        for i in range(3):
            run_one()
            print("第 %s 遍自检通过" % (i + 1))
        print("稳定运行 3 遍完成，请连接手机后执行 observe_10min 验证进入搜索页。")
        sys.exit(0)
    except Exception as e:
        print("自检报错:", e)
        import traceback
        traceback.print_exc()
        sys.exit(1)
