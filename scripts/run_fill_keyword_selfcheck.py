# -*- coding: utf-8 -*-
"""
填充关键字自检：Mock 设备模拟搜索栏与输入框，验证 _step_fill_keyword 两条路径：
1）selector 路径：搜索栏+输入框均由 selector 找到，set_text 输入；
2）hierarchy 兜底路径：仅搜索栏由 selector 找到，输入框由 hierarchy 找 EditText，click+send_keys 输入。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


class MockInputSelector:
    """可 set_text 的 Mock 输入框，供填充关键字使用"""

    def __init__(self, exists: bool = True, initial_text: str = ""):
        self._exists = exists
        self._text = initial_text

    @property
    def exists(self):
        return self._exists

    def click(self):
        pass

    def set_text(self, s: str):
        self._text = (s or "").strip()

    @property
    def info(self):
        return {"text": self._text}


class MockSelector:
    def __init__(self, exists: bool, info=None, is_input: bool = False):
        self._exists = exists
        self._info = info or {}
        self._is_input = is_input
        self._input = MockInputSelector(exists) if is_input else None

    @property
    def exists(self):
        return self._exists

    def click(self):
        pass

    def set_text(self, s: str):
        if self._input:
            self._input.set_text(s)

    @property
    def info(self):
        if self._input:
            return self._input.info
        return self._info


class MockDeviceFill:
    """模拟设备：首页、有搜索栏和输入框，支持 set_text（selector 路径）"""

    def __init__(self):
        self._tokens = ["搜索", "请输入"]
        self._pkg = "com.taobao.idlefish"

    def _match(self, text=None, description=None, textContains=None, descriptionContains=None):
        for t in (text, description, textContains, descriptionContains):
            if t is None:
                continue
            s = str(t).strip()
            if s in self._tokens or any(x in (s or "") for x in self._tokens):
                return True
        return False

    def __call__(
        self,
        text=None,
        description=None,
        textContains=None,
        descriptionContains=None,
        resourceId=None,
        resourceIdMatches=None,
        className=None,
        focused=None,
        instance=None,
        **kwargs,
    ):
        rid = (resourceId or resourceIdMatches or "").lower()
        clz = (className or "").lower()
        if "search" in rid or "search_bar" in rid or "default_search" in rid:
            return MockSelector(True)
        if "search_edit" in rid or (clz and "edittext" in clz):
            return MockSelector(True, is_input=True)
        if self._match(text=text, description=description, textContains=textContains, descriptionContains=descriptionContains):
            return MockSelector(True)
        return MockSelector(False)

    def dump_hierarchy(self):
        return '<?xml version="1.0"?><node/>'

    def window_size(self):
        return (1080, 2400)

    def press(self, key):
        pass


class MockDeviceHierarchyFallback:
    """模拟设备：搜索栏由 selector 找到，输入框仅 hierarchy 有 EditText，必须走 click+send_keys 兜底"""

    def __init__(self):
        self._tokens = ["搜索", "请输入"]
        self._pkg = "com.taobao.idlefish"
        self._sent_keys = []
        self._clicks = []

    def _match(self, text=None, description=None, textContains=None, descriptionContains=None):
        for t in (text, description, textContains, descriptionContains):
            if t is None:
                continue
            s = str(t).strip()
            if s in self._tokens or any(x in (s or "") for x in self._tokens):
                return True
        return False

    def __call__(
        self,
        text=None,
        description=None,
        textContains=None,
        descriptionContains=None,
        resourceId=None,
        resourceIdMatches=None,
        className=None,
        focused=None,
        instance=None,
        **kwargs,
    ):
        rid = (resourceId or resourceIdMatches or "").lower()
        clz = (className or "").lower()
        # 输入框相关：先判，一律不提供，迫使走 hierarchy 兜底
        if "search_edit" in rid or (clz and "edittext" in clz) or (focused is True) or "edit" in rid:
            return MockSelector(False)
        # 搜索栏（仅整块搜索区，非输入框）：有
        if "search" in rid or "search_bar" in rid or "default_search" in rid:
            return MockSelector(True)
        if self._match(text=text, description=description, textContains=textContains, descriptionContains=descriptionContains):
            return MockSelector(True)
        return MockSelector(False)

    def dump_hierarchy(self):
        # 返回带 EditText、bounds 的 XML，供 _find_edit_node_from_xml 解析
        return '''<?xml version="1.0"?>
<node>
  <node class="android.widget.EditText" resource-id="com.taobao.idlefish:id/search_edit" bounds="[100,200][800,280]" />
</node>'''

    def window_size(self):
        return (1080, 2400)

    def press(self, key):
        pass

    def click(self, x, y):
        self._clicks.append((x, y))

    def send_keys(self, text, clear=False):
        self._sent_keys.append((text, clear))


def run_one(log_cb=None):
    """跑一遍 selector 路径：Mock 搜索栏+输入框均存在"""
    from app.core.xianyu import XianyuDriver

    d = MockDeviceFill()
    driver = XianyuDriver(d, log_cb=log_cb)
    driver._current_keyword = "钢笔"
    ok = driver._step_fill_keyword("钢笔")
    if not ok:
        raise RuntimeError("_step_fill_keyword 返回 False（selector 路径）")
    return True


def run_one_hierarchy_fallback(log_cb=None):
    """跑一遍 hierarchy 兜底路径：仅 hierarchy 有 EditText，必须 click+send_keys（不预调 _find_edit，避免影响后续 dump）"""
    from app.core.xianyu import XianyuDriver

    d = MockDeviceHierarchyFallback()
    driver = XianyuDriver(d, log_cb=log_cb)
    driver._current_keyword = "钢笔"
    ok = driver._step_fill_keyword("钢笔")
    if not ok:
        raise RuntimeError("_step_fill_keyword 返回 False（hierarchy 兜底路径）")
    if not d._sent_keys:
        raise RuntimeError("hierarchy 兜底未调用 send_keys，输入未执行")
    texts = [t for t, _ in d._sent_keys]
    if "钢笔" not in texts:
        raise RuntimeError("send_keys 未传入关键字「钢笔」，实际: %s" % texts)
    return True


if __name__ == "__main__":
    log_cb = lambda level, msg, *args: print("[%s] %s" % (level, msg % args if args else msg))
    # 1）selector 路径 3 遍
    for i in range(3):
        run_one(log_cb=log_cb)
        print("第 %s 遍（selector 路径）通过" % (i + 1))
    # 2）hierarchy 兜底路径 2 遍
    for i in range(2):
        run_one_hierarchy_fallback(log_cb=log_cb)
        print("第 %s 遍（hierarchy 兜底路径）通过" % (i + 1))
    print("全部自检通过。")
    sys.exit(0)
