# -*- coding: utf-8 -*-
"""闲鱼 App 自动化：启动、搜索、列表点击、下单（基于 u2 控件/坐标）"""

import logging
import random
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional, List, Tuple, Any, Dict, Callable

from config import XIANYU_PACKAGE

# 超时截图回调：返回要保存的路径则执行截图并写入
OnTimeoutScreenshot = Optional[Callable[[], Optional[str]]]

# 列表解析超时（秒），防止 u2 遍历 RecyclerView 时卡死；超时后会再重试一次
LIST_PARSE_TIMEOUT = 30

logger = logging.getLogger("xianyu.driver")


def _pct_safe(s: str) -> str:
    """避免描述中含 % 导致日志 % 格式化异常。"""
    return (s or "").replace("%", "%%")


class XianyuDriver:
    """单设备上的闲鱼操作封装。log_cb(level, msg) 可选；on_dump(step_name, xml) 用于保存页面快照。"""

    def __init__(
        self,
        device: Any,
        log_cb: Optional[Callable[[str, str], None]] = None,
        on_dump: Optional[Callable[[str, str], None]] = None,
        on_timeout_screenshot: OnTimeoutScreenshot = None,
    ):
        self._d = device
        self._pkg = XIANYU_PACKAGE
        self._log = log_cb
        self._on_dump = on_dump
        self._on_timeout_screenshot = on_timeout_screenshot
        # 刷新流程跑偏时用此 keyword 重新走「首页→搜索→新发」恢复
        self._current_keyword: Optional[str] = None
        # 当前所在 tab：新发/降价/价格；仅新发、降价需解析商品，价格页不解析
        self._current_tab: Optional[str] = None

    def _dump_ui_if_debug(self, step_name: str) -> None:
        """若配置了 on_dump，则拉取当前页面层级 XML 并回调（便于保存后分析）"""
        if not self._on_dump:
            return
        try:
            xml = self._d.dump_hierarchy()
            self._on_dump(step_name, xml or "")
        except Exception as e:
            if self._log:
                self._log("WARNING", "dump 页面失败: %s" % e)

    def _log_step(self, level: str, msg: str, *args) -> None:
        text = msg % args if args else msg
        getattr(logger, level.lower(), logger.info)(msg, *args)
        if self._log:
            self._log(level.upper(), text)

    def is_app_installed(self) -> bool:
        """检查设备是否已安装闲鱼 App；多方式检测并写日志便于排查"""
        # 1) u2 app_info
        try:
            info = self._d.app_info(self._pkg)
            if info is not None and (isinstance(info, dict) or getattr(info, "get", None)):
                if isinstance(info, dict) and not info:
                    pass
                else:
                    return True
        except Exception as e:
            if self._log:
                self._log("DEBUG", "is_app_installed app_info 异常: %s", str(e))
        # 2) pm path 包名
        try:
            out = self._d.shell("pm path %s" % self._pkg)
            s = (out or "").strip()
            if s and "package:" in s:
                return True
        except Exception as e:
            if self._log:
                self._log("DEBUG", "is_app_installed pm path 异常: %s", str(e))
        # 3) pm list packages 中是否包含闲鱼包名或 idlefish
        try:
            out = self._d.shell("pm list packages")
            s = (out or "").strip()
            if s:
                for line in s.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line == "package:" + self._pkg:
                        return True
                    if "idlefish" in line.lower() or (self._pkg in line and line.startswith("package:")):
                        return True
        except Exception as e:
            if self._log:
                self._log("DEBUG", "is_app_installed pm list 异常: %s", str(e))
        if self._log:
            self._log("WARNING", "未检测到闲鱼安装（包名 %s）；若已安装请检查设备 shell 权限或包名是否一致", self._pkg)
        return False

    def close_all_apps(self) -> None:
        """定时运行前关闭手机已打开的应用：打开最近任务并点击「清除全部」类按钮"""
        try:
            self._log_step("INFO", "关闭已打开的应用")
            self._d.press("recent")
            time.sleep(1.2)
            clicked = False
            for text in ["清除全部", "关闭全部", "全部清除", "Clear all", "清除", "关闭"]:
                for sel in [self._d(text=text), self._d(textContains=text), self._d(description=text), self._d(descriptionContains=text)]:
                    if sel.exists:
                        sel.click()
                        clicked = True
                        time.sleep(0.5)
                        break
                if clicked:
                    break
            self._d.press("back")
            time.sleep(0.3)
        except Exception as e:
            if self._log:
                self._log("WARNING", "关闭应用异常: %s", str(e))

    def _do_start_app(self) -> None:
        """实际执行启动：优先 use_monkey=True（实测可稳定将闲鱼带到前台），异常时再试默认 app_start 与 shell monkey 兜底"""
        # 1) use_monkey=True：不依赖 mainActivity，多数设备上能正确拉到前台
        try:
            self._d.app_start(self._pkg, use_monkey=True)
            return
        except Exception as e:
            if self._log:
                self._log("WARNING", "app_start(use_monkey=True) 异常，尝试默认启动: %s", str(e))
        # 2) 默认 app_start（am start -n pkg/mainActivity）
        try:
            self._d.app_start(self._pkg)
            return
        except Exception as e:
            if self._log:
                self._log("WARNING", "app_start 异常，尝试 shell monkey: %s", str(e))
        # 3) shell monkey 兜底
        try:
            self._d.shell("monkey -p %s -c android.intent.category.LAUNCHER 1" % self._pkg)
        except Exception as e2:
            if self._log:
                self._log("WARNING", "monkey 启动失败: %s", str(e2))

    def start_app(self) -> bool:
        """启动闲鱼（若已在后台则切到前台），并等待直至处于前台。支持重试与 shell 兜底。"""
        try:
            try:
                if self._d.app_current().get("package") == self._pkg:
                    self._log_step("INFO", "闲鱼已在前台，跳过启动")
                    return True
                if self._is_xianyu_visible_from_ui():
                    self._log_step("INFO", "闲鱼已在前台（UI 层级），跳过启动")
                    return True
            except Exception:
                pass
            self._log_step("INFO", "启动闲鱼 App")
            for attempt in range(1, 5):
                if attempt > 1:
                    self._log_step("INFO", "第 %d 次尝试启动闲鱼", attempt)
                if attempt == 4:
                    # 最后一轮：先停掉闲鱼再启动，避免被其他前台应用抢占
                    try:
                        self._d.app_stop(self._pkg)
                        time.sleep(1.5)
                    except Exception as e:
                        if self._log:
                            self._log("DEBUG", "app_stop 兜底: %s", str(e))
                self._do_start_app()
                time.sleep(5)
                pid = self._d.app_wait(self._pkg, timeout=20, front=True)
                if pid:
                    self._log_step("INFO", "闲鱼已在前台运行")
                    return True
                time.sleep(2)
                try:
                    if self._d.app_current().get("package") == self._pkg:
                        self._log_step("INFO", "闲鱼已在前台（二次确认）")
                        return True
                    if self._is_xianyu_visible_from_ui():
                        self._log_step("INFO", "闲鱼已在前台（UI 层级）")
                        return True
                except Exception:
                    pass
            self._log_step(
                "WARNING",
                "闲鱼未切到前台（多应用或浮窗时可能无法识别），请将闲鱼置于手机前台，任务将自动重试",
            )
            return False
        except Exception as e:
            self._log_step("ERROR", "启动闲鱼失败: %s", str(e))
            return False

    def _is_xianyu_visible_from_ui(self) -> bool:
        """部分设备/ROM 下 dumpsys 焦点误报时，用 hierarchy 根节点 package 做二次判断。仅认根节点，不认子节点，避免启动器/推荐页里出现闲鱼图标时误判为闲鱼在前台。"""
        try:
            xml = self._d.dump_hierarchy()
            if not xml or not xml.strip():
                return False
            root = ET.fromstring(xml)
            pkg = (root.get("package") or "").strip()
            return pkg == self._pkg
        except Exception:
            return False

    def is_app_running(self) -> bool:
        """当前前台是否为闲鱼；多应用/浮窗时 dumpsys 可能报其他包，用 UI 层级根 package 做备用判断"""
        try:
            current = (self._d.app_current() or {}).get("package") or ""
            if current == self._pkg:
                return True
            self._log_step("INFO", "app_current(package)=%s 闲鱼pkg=%s", current or "(空)", self._pkg)
            # 界面实际是闲鱼但 app_current 报成其他包（如浮窗/多应用），用 hierarchy 根 package 再判一次
            if self._is_xianyu_visible_from_ui():
                self._log_step("INFO", "app_current 非闲鱼，但 UI 层级为闲鱼，按闲鱼在前台处理")
                return True
            # 闲鱼在运行但不在前台时，提示用户切到前台（日志会出现在前端控制台）
            try:
                running = self._d.app_list_running() or []
                if self._pkg in running:
                    self._log_step(
                        "WARNING",
                        "当前前台非闲鱼（多应用或浮窗时无法识别），请将闲鱼切到手机前台，任务将自动重试",
                    )
            except Exception:
                pass
            return False
        except Exception as e:
            self._log_step("WARNING", "检测前台失败: %s", str(e))
            return False

    def try_wait_front(self, timeout: float = 2) -> bool:
        """短时等待闲鱼处于前台；若已在或很快到前台则返回 True。用于误判时避免重复 app_start。"""
        try:
            pid = self._d.app_wait(self._pkg, timeout=timeout, front=True)
            return bool(pid)
        except Exception:
            return False

    def is_app_in_background(self) -> bool:
        """闲鱼是否已启动但在后台（非当前前台）；若 UI 层级已是闲鱼则视为前台非后台"""
        try:
            current = self._d.app_current().get("package")
            if current == self._pkg:
                return False
            if self._is_xianyu_visible_from_ui():
                return False
            return self._pkg in (self._d.app_list_running() or [])
        except Exception:
            return False

    def go_home(self) -> None:
        """回到闲鱼首页：优先点击底部「闲鱼」或「首页」tab，否则多按返回；仍无则重新拉起 App 兜底"""
        for tab_text in ["闲鱼", "首页"]:
            tab_home = self._d(text=tab_text)
            if tab_home.exists:
                tab_home.click()
                time.sleep(1.5)
                return
        for _ in range(6):
            self._d.press("back")
            time.sleep(0.4)
        time.sleep(0.8)
        # 仍无底部 tab 时重新启动 App，便于回到主界面
        for tab_text in ["闲鱼", "首页"]:
            if self._d(text=tab_text).exists:
                self._d(text=tab_text).click()
                time.sleep(1.5)
                return
        try:
            self._d.app_start(self._pkg, use_monkey=True)
            time.sleep(3)
        except Exception as e:
            if self._log:
                self._log("WARNING", "go_home 重启 App 兜底异常: %s", str(e))

    def is_logged_in(self) -> bool:
        """已登录：首页特征 或 在「我的」页能获取到用户信息（鱼力值/我的收藏等）"""
        time.sleep(1)
        # 首页特征：搜索框、底部 tab
        for sel in [
            self._d(description="搜索", className="android.widget.EditText"),
            self._d(className="android.widget.EditText"),
            self._d(text="搜索"),
            self._d(description="搜索"),
            self._d(resourceId=f"{self._pkg}:id/search_edit"),
            self._d(resourceIdMatches=".*search.*"),
            self._d(text="首页"),
            self._d(text="发布"),
            self._d(text="消息"),
            self._d(text="我的"),
        ]:
            if sel.exists:
                return True
        # 「我的」页能获取到用户信息即视为已登录（鱼力值、我的收藏、在闲鱼赚了等）
        for sel in [
            self._d(textContains="鱼力值"),
            self._d(textContains="我的收藏"),
            self._d(textContains="我的关注"),
            self._d(textContains="我的交易"),
            self._d(textContains="在闲鱼赚了"),
            self._d(textContains="历史浏览"),
        ]:
            if sel.exists:
                return True
        # 明确是登录页：手机号登录、淘宝账号登录等
        for sel in [
            self._d(textContains="手机号登录"),
            self._d(textContains="淘宝账号登录"),
            self._d(textContains="验证码登录"),
        ]:
            if sel.exists:
                self._log_step("INFO", "检测到未登录界面")
                return False
        return True

    # 流程每步最大重试次数，任一步 3 次仍失败则退回首页
    _STEP_RETRIES = 3

    def _is_on_home(self) -> bool:
        """是否在闲鱼首页。先看是否有应用内专属控件（启动器不会有），有则直接判为首页；否则再结合 hierarchy 与 tab 判断，避免 hierarchy 未上报 package 时误判失败。"""
        # 应用内专属 resourceId，出现即可认为在闲鱼首页（不依赖 hierarchy）
        for sel in [
            self._d(resourceId=f"{self._pkg}:id/search_bar_layout"),
            self._d(resourceId=f"{self._pkg}:id/default_search"),
            self._d(resourceId=f"{self._pkg}:id/search_edit"),
        ]:
            if sel.exists:
                return True
        # 搜索框或底部 tab：需先确认当前是闲鱼界面（避免启动器上的「闲鱼」图标误判）
        if not self._is_xianyu_visible_from_ui():
            return False
        for sel in [
            self._d(description="搜索", className="android.widget.EditText"),
            self._d(text="闲鱼"),
            self._d(text="首页"),
            self._d(text="发布"),
            self._d(text="我的"),
        ]:
            if sel.exists:
                return True
        return False

    def _step_home(self) -> bool:
        """步骤1：定位首页（先回首页再轮询确认）"""
        self.go_home()
        for _ in range(3):
            time.sleep(1.2)
            self._dump_ui_if_debug("01_after_go_home")
            if self._is_on_home():
                return True
            self.go_home()
        return False

    def _is_keyword_already_in_search_input(self, keyword: str) -> bool:
        """当前是否已处于「搜索框已弹出且关键字已填入」状态；仅当输入框内容与关键字完全一致时才跳过，避免历史搜索被误判"""
        if not (keyword or "").strip():
            return False
        kw = keyword.strip().replace("\u200b", "").replace(" ", "")
        try:
            if self.is_on_new_drop_page():
                return True
            # 依次检查 search_edit、focused EditText、任意 EditText，任一内容与关键字一致则跳过
            for sel in [
                self._d(resourceId=f"{self._pkg}:id/search_edit"),
                self._d(className="android.widget.EditText", focused=True),
                self._d(className="android.widget.EditText"),
            ]:
                if not sel.exists:
                    continue
                try:
                    raw = getattr(sel, "info", None)
                    info = (raw() if callable(raw) else raw) or {}
                    text = (info.get("text") or "").strip().replace("\u200b", "").replace(" ", "")
                    if not kw:
                        continue
                    if text == kw or (text.startswith(kw) and (len(text) == len(kw) or (len(text) > len(kw) and text[len(kw):len(kw)+1] in (" ", "\n")))):
                        return True
                except Exception:
                    pass
            return False
        except Exception:
            return False

    @staticmethod
    def _bounds_center(elem: "ET.Element") -> Optional[Tuple[int, int]]:
        """从 node 的 bounds 属性解析中心点；格式为 [x1,y1][x2,y2]（允许内部空格）"""
        b = (elem.attrib.get("bounds") or "").strip()
        m = re.match(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]\[\s*(\d+)\s*,\s*(\d+)\s*\]", b)
        if m:
            x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            return ((x1 + x2) // 2, (y1 + y2) // 2)
        return None

    def _find_search_node_from_xml(self, root: "ET.Element") -> "Optional[ET.Element]":
        """从 hierarchy 根中找第一个像「搜索区」的节点：resource-id 含 search，或 text/desc 含 请输入/搜索"""
        pkg_prefix = (self._pkg + ":id/").lower()
        for elem in root.iter("node"):
            rid = (elem.attrib.get("resource-id") or elem.attrib.get("resourceId") or "").lower()
            text = (elem.attrib.get("text") or "").strip()
            desc = (elem.attrib.get("content-desc") or "").strip()
            combined = text + " " + desc
            if "search" in rid and pkg_prefix in rid:
                if self._bounds_center(elem):
                    return elem
            if "请输入" in combined or ("搜索" in combined and "拍照" not in combined and "以图" not in combined):
                if self._bounds_center(elem):
                    return elem
        return None

    def _find_edit_node_from_xml(self, root: "ET.Element", window_height: int = 2400) -> "Optional[ET.Element]":
        """从 hierarchy 中找第一个像搜索输入框的 EditText（优先上半屏、rid 含 search/edit）；兼容 tag=node+class 与 tag=EditText"""
        candidates = []
        for elem in root.iter():
            clz = (elem.attrib.get("class") or elem.attrib.get("className") or "").strip()
            tag = (elem.tag or "").strip()
            if isinstance(tag, str) and "}" in tag:
                tag = tag.split("}", 1)[-1]
            if "EditText" not in clz and "EditText" not in tag:
                continue
            center = self._bounds_center(elem)
            if not center:
                continue
            _, cy = center
            rid = (elem.attrib.get("resource-id") or elem.attrib.get("resourceId") or "").lower()
            score = 0
            if "search" in rid or "edit" in rid:
                score += 2
            if cy < window_height * 0.5:
                score += 1
            candidates.append((score, elem))
        if not candidates:
            return None
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    def _find_search_input_like_node_from_xml(self, root: "ET.Element", window_height: int = 2400) -> "Optional[ET.Element]":
        """无 EditText 时兜底：找上半屏、可点击/可聚焦、横向条形节点（闲鱼搜索页为自定义 View）"""
        pkg = (self._pkg or "").lower()
        candidates = []
        for elem in root.iter():
            center = self._bounds_center(elem)
            if not center:
                continue
            cx, cy = center
            if cy > window_height * 0.25:
                continue
            b = (elem.attrib.get("bounds") or "").strip()
            m = re.match(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]\[\s*(\d+)\s*,\s*(\d+)\s*\]", b)
            if not m:
                continue
            x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            w, h = x2 - x1, y2 - y1
            if w < 300 or h < 20 or h > 200:
                continue
            clickable = (elem.attrib.get("clickable") or "").lower() == "true"
            focusable = (elem.attrib.get("focusable") or "").lower() == "true"
            if not clickable and not focusable:
                continue
            node_pkg = (elem.attrib.get("package") or "").lower()
            score = 0
            if pkg and pkg in node_pkg:
                score += 2
            if clickable:
                score += 1
            if focusable:
                score += 1
            if 400 < w < 1000 and 30 < h < 120:
                score += 2
            candidates.append((score, elem))
        if not candidates:
            return None
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    def _step_fill_keyword(self, keyword: str) -> bool:
        """步骤2：填充关键字（点搜索区、输入关键词或点联想词，不点搜索按钮）。若已弹出搜索框且关键字已填入则直接成功"""
        try:
            self._log_step("INFO", "填充关键字: %s", keyword)
            if self._is_keyword_already_in_search_input(keyword or ""):
                self._log_step("INFO", "搜索框已弹出且关键字已填入，跳过填充，直接进入新发流程")
                return True
            self._dump_ui_if_debug("02_before_search_click")
            # 搜索栏：优先 resourceId，再 placeholder 文案（请输入），再 description；避免点右侧相机
            search = None
            search_selector_names = [
                "search_bar_layout", "default_search", "search_edit", "resourceIdMatches.*search",
                "description=搜索+EditText", "descriptionContains=搜索+EditText", "textContains=请输入+EditText",
                "descriptionContains=请输入+EditText", "textContains=请输入", "descriptionContains=请输入",
                "className=EditText", "description=搜索", "descriptionContains=搜索", "text=搜索", "textContains=搜索",
            ]
            for idx, selector in enumerate([
                self._d(resourceId=f"{self._pkg}:id/search_bar_layout"),
                self._d(resourceId=f"{self._pkg}:id/default_search"),
                self._d(resourceId=f"{self._pkg}:id/search_edit"),
                self._d(resourceIdMatches=f"{self._pkg}:id/.*search.*"),
                self._d(description="搜索", className="android.widget.EditText"),
                self._d(descriptionContains="搜索", className="android.widget.EditText"),
                self._d(textContains="请输入", className="android.widget.EditText"),
                self._d(descriptionContains="请输入", className="android.widget.EditText"),
                self._d(textContains="请输入"),
                self._d(descriptionContains="请输入"),
                self._d(className="android.widget.EditText"),
                self._d(description="搜索"),
                self._d(descriptionContains="搜索"),
                self._d(text="搜索"),
                self._d(textContains="搜索"),
            ]):
                if selector.exists and not self._is_camera_or_photo_button(selector):
                    search = selector
                    self._log_step("INFO", "搜索区由 selector[%s] 命中: %s", idx, search_selector_names[idx] if idx < len(search_selector_names) else "?")
                    break
            if not search or not search.exists:
                # 兜底：从 hierarchy 解析搜索区节点并按坐标点击
                try:
                    xml_str = self._d.dump_hierarchy()
                    if xml_str:
                        root = ET.fromstring(xml_str)
                        search_node = self._find_search_node_from_xml(root)
                        if search_node:
                            center = self._bounds_center(search_node)
                            if center:
                                self._log_step("INFO", "通过 hierarchy 定位搜索区并点击坐标 %s", center)
                                self._d.click(center[0], center[1])
                                time.sleep(0.8)
                            else:
                                self._log_step("WARNING", "未找到搜索框")
                                return False
                        else:
                            self._log_step("WARNING", "未找到搜索框")
                            return False
                    else:
                        self._log_step("WARNING", "未找到搜索框")
                        return False
                except Exception as e:
                    self._log_step("WARNING", "未找到搜索框（hierarchy 兜底异常: %s）", str(e))
                    return False
            else:
                search.click()
                time.sleep(0.8)
            self._dump_ui_if_debug("03_after_search_click")
            # 输入框：点击后可能延迟出现，多轮等待；优先 search_edit，再 focused/任意 EditText
            input_edit = None
            for round_idx, round_wait in enumerate((0.0, 0.3, 0.6, 1.0, 1.5)):
                if round_wait > 0:
                    time.sleep(round_wait)
                if round_idx == 3 and (not input_edit or not input_edit.exists):
                    # 仍未找到时再点一次搜索区，部分机型需二次点击才弹出输入
                    try:
                        if search.exists:
                            search.click()
                            time.sleep(0.5)
                    except Exception:
                        pass
                input_selectors = [
                    (f"{self._pkg}:id/search_edit", self._d(resourceId=f"{self._pkg}:id/search_edit")),
                    ("EditText+focused", self._d(className="android.widget.EditText", focused=True)),
                    (".*search.*+EditText", self._d(resourceIdMatches=f"{self._pkg}:id/.*search.*", className="android.widget.EditText")),
                    (".*edit.*+EditText", self._d(resourceIdMatches=".*edit.*", className="android.widget.EditText")),
                    ("description=搜索+EditText", self._d(description="搜索", className="android.widget.EditText")),
                    ("descriptionContains=搜索+EditText", self._d(descriptionContains="搜索", className="android.widget.EditText")),
                    ("textContains=请输入+EditText", self._d(textContains="请输入", className="android.widget.EditText")),
                    ("descriptionContains=请输入+EditText", self._d(descriptionContains="请输入", className="android.widget.EditText")),
                    ("className=EditText", self._d(className="android.widget.EditText")),
                ]
                for name, sel in input_selectors:
                    if sel.exists:
                        input_edit = sel
                        self._log_step("INFO", "输入框由 selector 命中: %s（轮次 %s）", name, round_idx + 1)
                        break
                if input_edit and input_edit.exists:
                    break
            if input_edit and input_edit.exists:
                try:
                    input_edit.set_text("")
                    time.sleep(0.12)
                except Exception as e:
                    self._log_step("WARNING", "清空搜索框失败: %s", str(e))
                for try_idx in range(2):
                    try:
                        input_edit.set_text(keyword)
                        time.sleep(0.1)
                        self._log_step("INFO", "set_text 成功，关键字已填入")
                        return True
                    except Exception as e:
                        self._log_step("WARNING", "set_text 失败（第 %s 次）: %s", try_idx + 1, str(e))
                        time.sleep(0.25)
                try:
                    self._d(focused=True).set_text("")
                    time.sleep(0.12)
                    self._d(focused=True).set_text(keyword)
                    self._log_step("INFO", "焦点 set_text 成功")
                    return True
                except Exception as e2:
                    self._log_step("WARNING", "焦点输入关键词失败: %s", str(e2))
                    return False
            self._log_step("INFO", "未通过 selector 找到输入框，走 hierarchy 兜底")
            # 兜底：从 hierarchy 找 EditText 并按坐标点击聚焦后 send_keys
            try:
                xml_str = self._d.dump_hierarchy()
                if not xml_str:
                    raise ValueError("dump_hierarchy 为空")
                root = ET.fromstring(xml_str)
                try:
                    w, h = self._d.window_size()
                except Exception:
                    h = 2400
                edit_node = self._find_edit_node_from_xml(root, h)
                if edit_node is None:
                    edit_node = self._find_search_input_like_node_from_xml(root, h)
                    if edit_node is not None:
                        self._log_step("INFO", "hierarchy 无 EditText，使用「输入条」兜底节点")
                self._log_step(
                    "DEBUG", "hierarchy 兜底: xml_len=%s edit_node=%s",
                    len(xml_str), "有" if (edit_node is not None) else "无"
                )
                # 用 is not None 判断：Element 在某些环境下可能为 falsy
                if edit_node is not None:
                    center = self._bounds_center(edit_node)
                    bounds_raw = (edit_node.attrib.get("bounds") or edit_node.attrib.get("resource-id") or "")
                    self._log_step("INFO", "hierarchy 找到 EditText bounds=%s center=%s", bounds_raw[:60] if bounds_raw else "", center)
                    if not center:
                        self._log_step("WARNING", "hierarchy 兜底: 输入框节点无有效 bounds，跳过点击")
                    if center:
                        self._log_step("INFO", "通过 hierarchy 定位输入框并点击坐标 %s，send_keys 输入", center)
                        self._d.click(center[0], center[1])
                        time.sleep(0.6)
                        send_ok = False
                        try:
                            self._d.send_keys(keyword, clear=True)
                            send_ok = True
                            self._log_step("INFO", "send_keys(keyword, clear=True) 调用成功")
                        except Exception as e_send:
                            self._log_step("WARNING", "send_keys 失败，尝试焦点 set_text: %s", str(e_send))
                            try:
                                self._d(focused=True).set_text(keyword)
                                send_ok = True
                                self._log_step("INFO", "焦点 set_text 兜底成功")
                            except Exception as e2:
                                self._log_step("WARNING", "焦点 set_text 也失败: %s", str(e2))
                        time.sleep(0.2)
                        if self._is_keyword_already_in_search_input(keyword):
                            self._log_step("INFO", "校验：关键字已在输入框中")
                            return True
                        if send_ok:
                            self._log_step("INFO", "send_keys 已执行，尝试键盘提交以进入结果页")
                            self._step_submit_by_keyboard()
                            time.sleep(1.0)
                            if self._d(text="新发").exists or self._d(description="新发").exists:
                                self._log_step("INFO", "已进入结果页")
                            return True
                        # 偶发未刷新，再试一次
                        try:
                            self._d.send_keys(keyword, clear=True)
                            time.sleep(0.15)
                        except Exception:
                            pass
                        self._step_submit_by_keyboard()
                        time.sleep(0.8)
                        return True
            except Exception as e3:
                self._log_step("WARNING", "hierarchy 输入框兜底异常: %s", str(e3))
            self._dump_ui_if_debug("04_when_input_not_found")
            self._log_step("WARNING", "未找到可输入的搜索框")
            return False
        except Exception as e:
            self._log_step("WARNING", "填充关键字异常: %s", str(e))
            return False

    def _is_camera_or_photo_button(self, elem) -> bool:
        """根据 resourceId/description/text 判断是否为「拍照」「以图搜索」等相机类按钮，避免误点"""
        try:
            raw = getattr(elem, "info", None)
            info = (raw() if callable(raw) else raw) or {}
            rid = (info.get("resourceId") or "").lower()
            desc = str(info.get("contentDescription") or "") + str(info.get("text") or "")
            # resourceId 含相机/拍照/以图 相关关键字则判定为相机按钮
            if any(k in rid for k in ("camera", "photo", "image", "pic", "scan", "picture")):
                return True
            # 文案含拍照、相机、以图、扫一扫、识图 等
            if any(k in desc for k in ("拍照", "相机", "以图", "扫一扫", "识图")):
                return True
            return False
        except Exception:
            return False

    def _step_click_search(self) -> bool:
        """不再点击「搜索」按钮（易误点相机），改为键盘提交；若已在结果页则直接成功"""
        try:
            for new_btn in [self._d(text="新发"), self._d(description="新发")]:
                if new_btn.exists:
                    self._log_step("INFO", "已在结果页，无需提交")
                    return True
            self._log_step("INFO", "不点搜索按钮，改用键盘提交")
            return self._step_submit_by_keyboard()
        except Exception as e:
            self._log_step("WARNING", "提交异常: %s", str(e))
            return False

    def _step_click_new(self) -> bool:
        """步骤4：点击新发（支持精确匹配与包含匹配，避免因空格等未找到）"""
        try:
            self._log_step("INFO", "点击「新发」")
            for new_btn in [
                self._d(text="新发"),
                self._d(description="新发"),
                self._d(textContains="新发"),
                self._d(descriptionContains="新发"),
            ]:
                if new_btn.exists:
                    new_btn.click()
                    time.sleep(1.0)
                    self._current_tab = "新发"
                    return True
            self._log_step("WARNING", "未找到「新发」按钮")
            return False
        except Exception as e:
            self._log_step("WARNING", "点击新发异常: %s", str(e))
            return False

    def _verify_after_fill_keyword(self, keyword: Optional[str] = None) -> bool:
        """填充关键字后校验：应出现搜索按钮、已在结果页，或关键字已填入（可直接走新发）"""
        time.sleep(0.2)
        if keyword and self._is_keyword_already_in_search_input(keyword):
            return True
        if self._d(resourceId=f"{self._pkg}:id/search_btn").exists:
            return True
        for sel in [self._d(text="搜索"), self._d(description="搜索")]:
            if sel.exists:
                return True
        if self._d(text="新发").exists or self._d(description="新发").exists:
            return True
        return False

    def _step_submit_by_keyboard(self) -> bool:
        """填充关键字后通过键盘提交（不点右侧按钮，避免误点相机）；按回车或搜索键"""
        try:
            self._log_step("INFO", "按键盘提交搜索（不点右侧按钮）")
            for key in ("enter", "search"):
                try:
                    self._d.press(key)
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue
            return True
        except Exception as e:
            self._log_step("WARNING", "键盘提交异常: %s", str(e))
            return False

    def _verify_after_submit_keyboard(self) -> bool:
        """键盘提交后校验：应进入带新发/降价的结果页或至少出现新发 tab"""
        time.sleep(0.6)
        return self.is_on_new_drop_page()

    def _verify_after_click_new(self) -> bool:
        """点击新发后校验：应在结果列表页"""
        time.sleep(0.5)
        if not self.is_on_new_drop_page():
            return False
        return True

    def _search_from_current_page(self, keyword: str) -> bool:
        """
        在当前页直接查找关键字录入框并搜索，不执行回退/回首页。
        填充关键字后直接点「新发」（不点搜索按钮）；未到搜索页再兜底键盘提交后点新发。
        """
        if not (keyword or "").strip():
            return False
        self._log_step("INFO", "当前页直接搜索: %s", keyword.strip())
        if not self._step_fill_keyword(keyword.strip()):
            return False
        time.sleep(0.25)
        self._step_click_new()
        time.sleep(0.6)
        if self.is_on_new_drop_page():
            return True
        self._log_step("INFO", "直接点新发未到搜索页，键盘提交后重试")
        if not self._step_submit_by_keyboard():
            return False
        time.sleep(0.6)
        self._step_click_new()
        time.sleep(0.5)
        return self.is_on_new_drop_page()

    def ensure_home_then_search(self, keyword: str) -> bool:
        """从 App 开始定位：定位首页→填充关键字→直接点新发（不点搜索按钮、不键盘提交）"""
        self._log_step("INFO", "从 App 开始定位：定位首页 → 填充关键字 → 新发，最多重试 %d 次", self._STEP_RETRIES)
        steps = [
            ("定位首页", lambda: self._step_home(), None),
            ("填充关键字", lambda: self._step_fill_keyword(keyword), lambda: self._verify_after_fill_keyword(keyword)),
            ("新发", lambda: self._step_click_new(), self._verify_after_click_new),
        ]
        for step_name, step_fn, verify_fn in steps:
            for attempt in range(1, self._STEP_RETRIES + 1):
                if not step_fn():
                    self._log_step("WARNING", "步骤「%s」第 %d 次执行失败，重试", step_name, attempt)
                    continue
                if verify_fn is not None:
                    if not verify_fn():
                        self._log_step("WARNING", "步骤「%s」第 %d 次校验未通过，重试", step_name, attempt)
                        continue
                break
            else:
                self._log_step("ERROR", "步骤「%s」重试 %d 次后仍失败，退回首页", step_name, self._STEP_RETRIES)
                self.go_home()
                return False
        if not self.is_on_new_drop_page():
            self._log_step("INFO", "直接点新发未到搜索页，尝试键盘提交后点新发")
            self._step_submit_by_keyboard()
            time.sleep(0.6)
            self._step_click_new()
            time.sleep(0.5)
        return self.is_on_new_drop_page()

    def search_keyword(self, keyword: str) -> bool:
        """兼容旧调用：定位到首页后执行关键字搜索（调用前需已安装、已启动、已登录）"""
        return self.ensure_home_then_search(keyword)

    def _collect_text_from_node(self, node, depth: int = 0) -> str:
        """递归收集节点及其子节点的文本；限制深度防止深层遍历卡死"""
        if depth > 5:
            return ""
        texts = []
        try:
            t = node.get_text() or node.info.get("contentDescription") or ""
            if t and t.strip():
                texts.append(t.strip())
        except Exception:
            pass
        try:
            for child in node.child():
                texts.append(self._collect_text_from_node(child, depth + 1))
        except Exception:
            pass
        return " ".join(texts)

    def _collect_text_shallow(self, node) -> str:
        """仅收集节点自身及直接子节点 text/desc，避免深递归卡死（用于商品列表）"""
        parts = []
        try:
            t = node.get_text() or node.info.get("contentDescription") or ""
            if t and t.strip():
                parts.append(t.strip())
        except Exception:
            pass
        try:
            for i in range(12):
                child = node.child(index=i)
                if not child.exists:
                    break
                c = child.get_text() or child.info.get("contentDescription") or ""
                if c and c.strip():
                    parts.append(c.strip())
        except Exception:
            pass
        return " ".join(parts)

    @staticmethod
    def _parse_item_user_desc_price(raw: str) -> Dict[str, Any]:
        """从单条 raw 文本拆出 description、price；不提取用户信息"""
        raw = (raw or "").strip()
        description, price = raw[:512] if raw else "", None
        # 价格：匹配 ¥ 或 元 后的数字
        m = re.search(r"[¥]\s*[\d,]+\.?\d*|[\d,]+\.?\d*\s*元", raw)
        if m:
            num = re.search(r"[\d,]+\.?\d*", m.group().replace(",", ""))
            if num:
                try:
                    price = float(num.group())
                except ValueError:
                    pass
        return {"user": "", "description": description, "price": price}

    @staticmethod
    def _find_list_root(root: "ET.Element") -> "Optional[ET.Element]":
        """从 hierarchy 中定位商品列表根节点：优先 nested_recycler_view，否则取子节点最多的 RecyclerView（兼容新发/降价等 tab）"""
        rv = None
        for elem in root.iter("node"):
            rid = elem.attrib.get("resource-id") or elem.attrib.get("resourceId") or ""
            if rid == "com.taobao.idlefish:id/nested_recycler_view":
                rv = elem
                break
        if rv is not None:
            return rv
        # 新发页等可能用其他 id：取 class 含 RecyclerView 且直接子 node 较多的作为列表
        candidates = []
        for elem in root.iter("node"):
            clz = elem.attrib.get("class", "")
            if "RecyclerView" not in clz:
                continue
            children = elem.findall("node")
            if len(children) >= 2:
                candidates.append((len(children), elem))
        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
        return None

    @staticmethod
    def _parse_items_from_hierarchy_xml(xml_str: str) -> List[Dict[str, Any]]:
        """从 hierarchy XML 解析商品列表，兼容新发/降价/综合等 tab。
        描述：TextView 长文案或 resource-id 含 title/content；价格：纯数字或 price 控件。"""
        items = []
        try:
            root = ET.fromstring(xml_str)
            rv = XianyuDriver._find_list_root(root)
            if rv is None:
                return []
            skip_phrases = ("刚刚发布", "分钟前发布", "人想要", "百分百好评", "卖家信用", "¥", "元")
            min_desc_len = 6  # 新发页标题可能较短，放宽到 6 字
            for rv_index, child in enumerate(rv.findall("node")):
                # 收集该条下所有 TextView 的 text 与 content-desc，以及带 id 的文案（新发页可能用 content_title/price）
                all_texts = []
                title_from_id = ""
                price_from_id = None
                for node in child.iter("node"):
                    clz = node.attrib.get("class", "")
                    rid = (node.attrib.get("resource-id") or node.attrib.get("resourceId") or "").lower()
                    t = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
                    if t:
                        all_texts.append(t)
                    if "TextView" in clz or t:
                        if "title" in rid or "content" in rid:
                            if len(t) > len(title_from_id):
                                title_from_id = t
                        if "price" in rid:
                            clean = t.replace(",", "").replace("¥", "").strip()
                            if re.match(r"^[\d,]+\.?\d*$", clean.replace(",", "")):
                                try:
                                    price_from_id = float(clean.replace(",", ""))
                                except ValueError:
                                    pass
                if not all_texts and not title_from_id:
                    continue
                if any("为您挑选" in t for t in all_texts) and not any(
                    re.match(r"^[\d,]+\.?\d*$", (t or "").replace(",", "")) for t in all_texts
                ) and price_from_id is None:
                    continue
                # 价格：先取 price 控件，再取纯数字 TextView
                price_val = price_from_id
                if price_val is None:
                    for t in all_texts:
                        clean = (t or "").replace(",", "")
                        if re.match(r"^[\d]+\.?\d*$", clean):
                            try:
                                price_val = float(clean)
                                break
                            except ValueError:
                                pass
                # 描述：优先 resource-id 含 title/content，再取最长非跳过文案（≥min_desc_len）
                desc = title_from_id[:512] if title_from_id else ""
                for t in all_texts:
                    if not t or t in skip_phrases or re.match(r"^[\d,]+\.?\d*$", (t or "").replace(",", "")):
                        continue
                    if any(w in t for w in skip_phrases) and len(t) < 30:
                        continue
                    if "为您挑选" in t:
                        continue
                    if len(t) >= min_desc_len and len(t) > len(desc):
                        desc = t
                if not desc and all_texts:
                    # 无长文案时取最长非数字非跳过文本（≥3 字）
                    for t in all_texts:
                        if len(t) >= 3 and not re.match(r"^[\d,]+\.?\d*$", (t or "").replace(",", "")) and "为您挑选" not in t:
                            if len(t) > len(desc):
                                desc = t
                if desc and price_val is not None:
                    items.append({"description": (desc or "")[:512], "price": price_val, "rv_index": rv_index})
        except Exception:
            pass
        return items

    def _get_items_from_xml(self) -> List[Dict[str, Any]]:
        """拉取当前页 hierarchy XML，按固定 node 规则解析出商品列表（描述+价格）"""
        try:
            xml_str = self._d.dump_hierarchy()
            if xml_str:
                return self._parse_items_from_hierarchy_xml(xml_str)
        except Exception:
            pass
        return []

    def _get_search_result_items_impl(self) -> List[Dict[str, Any]]:
        """从 hierarchy XML 固定 node 解析商品列表（nested_recycler_view 下描述+价格）"""
        return self._get_items_from_xml()

    def get_search_result_items(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """从当前搜索结果页解析商品列表；limit 仅取前 N 条（如新发页仅需前 3 条）"""
        try:
            self._log_step("INFO", "解析商品列表（RecyclerView/ListView）" + ("，仅前 %s 条" % limit if limit else ""))
            # 解析前 dump 当前页 UI 到 logs/，便于根据 XML 优化提取（需开启 DEBUG_DUMP_UI）
            self._dump_ui_if_debug("05_new_list_page")
            items = []
            for attempt in range(2):
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(self._get_search_result_items_impl)
                    try:
                        items = fut.result(timeout=LIST_PARSE_TIMEOUT)
                        break
                    except FuturesTimeoutError:
                        if attempt == 0:
                            self._log_step("INFO", "解析超时（%ss），重试一次", LIST_PARSE_TIMEOUT)
                        else:
                            self._log_step("WARNING", "解析商品列表超时（%ss），跳过本页", LIST_PARSE_TIMEOUT)
                            if self._on_timeout_screenshot:
                                try:
                                    path = self._on_timeout_screenshot()
                                    if path:
                                        self._d.screenshot(path)
                                        self._log_step("INFO", "已保存超时截图: %s", path)
                                except Exception as e:
                                    if self._log:
                                        self._log("WARNING", "保存超时截图失败: %s" % e)
                            return []
            if limit is not None and limit > 0:
                items = items[:limit]
            if items:
                self._log_step("INFO", "当前页解析到 %s 条", len(items))
                for i, it in enumerate(items, 1):
                    # XML 解析已带 description/price，无需再从 raw 拆
                    if it.get("description") is None or it.get("price") is None:
                        raw = (it.get("text") or "") + (it.get("desc") or "")
                        parsed = self._parse_item_user_desc_price(raw)
                        it["description"] = parsed["description"]
                        it["price"] = parsed["price"]
                    desc = (it.get("description") or "")[:60]
                    price = it.get("price")
                    price_str = str(price) if price is not None else "-"
                    self._log_step("INFO", "解析商品[%s] 描述=%s 价格=%s", i, _pct_safe(desc), price_str)
            return items
        except Exception as e:
            self._log_step("ERROR", "解析列表异常: %s", str(e))
            return []

    def _find_focusable_child(self, card) -> "Optional[Any]":
        """在 card 下查找 focusable=true 的子节点（先直接子节点，再孙子节点），供点击用"""
        try:
            for i in range(12):
                sub = card.child(index=i)
                if not sub.exists:
                    break
                focusable = sub.info.get("focusable")
                if focusable is True or focusable == "true":
                    return sub
            for i in range(12):
                sub = card.child(index=i)
                if not sub.exists:
                    break
                for j in range(8):
                    sub2 = sub.child(index=j)
                    if not sub2.exists:
                        break
                    focusable = sub2.info.get("focusable")
                    if focusable is True or focusable == "true":
                        return sub2
        except Exception:
            pass
        return None

    def click_item_at_index_and_buy(self, list_index: int = 0, rv_index: Optional[int] = None) -> Tuple[bool, str]:
        """
        点击商品进入详情并尝试下单。优先用 rv_index（解析时保留的 RV 子节点下标），否则用 list_index+1。
        返回 (是否成功点击并进入详情, 结果描述)
        """
        try:
            # 解析当前列表，打印即将点击的商品描述、价格、idx
            try:
                items = self.get_search_result_items(limit=20)
                idx = rv_index if rv_index is not None else list_index
                item = None
                if rv_index is not None:
                    item = next((it for it in (items or []) if it.get("rv_index") == rv_index), None)
                if item is None and items and 0 <= list_index < len(items):
                    item = items[list_index]
                if item is not None:
                    self._log_step(
                        "INFO",
                        "商品 idx=%s 描述=%s 价格=%s",
                        idx,
                        _pct_safe(str(item.get("description") or item.get("text") or "")),
                        item.get("price"),
                    )
            except Exception:
                pass
            rv = self._d(
                packageName=self._pkg,
                resourceId="com.taobao.idlefish:id/nested_recycler_view",
                className="androidx.recyclerview.widget.RecyclerView",
            )
            click_index = rv_index if rv_index is not None else (list_index + 1)
            if rv.exists:
                card = rv.child(index=click_index)
                if card.exists:
                    self._dump_ui_if_debug("06_before_click_card_%s" % click_index)
                    try:
                        info = card.info
                        bounds = info.get("bounds") or info.get("visibleBounds")
                        self._log_step("INFO", "点击第 %s 个商品进入详情（RV index=%s）bounds=%s",
                                       list_index + 1, click_index, bounds)
                    except Exception:
                        self._log_step("INFO", "点击第 %s 个商品进入详情（RV index=%s）", list_index + 1, click_index)
                    # 优先点击 card 下 focusable=true 的子节点（闲鱼卡内层才响应点击）
                    click_target = self._find_focusable_child(card)
                    if click_target is not None:
                        click_target.click()
                    else:
                        card.click()
                    time.sleep(0.3)
                    self._dump_ui_if_debug("07_after_click_card_%s" % click_index)
                    return self._do_buy_in_detail()
            # 兜底：新发页等可能用其他 id，先试 resourceId 含 list/nested 的 RV，再试通用 RecyclerView
            self._log_step("INFO", "未找到 nested_recycler_view，尝试其他列表控件 index=%s", click_index)
            for rv_sel in [
                self._d(resourceIdMatches=".*nested.*", className="androidx.recyclerview.widget.RecyclerView"),
                self._d(resourceIdMatches=".*list.*", className="androidx.recyclerview.widget.RecyclerView"),
                self._d(className="androidx.recyclerview.widget.RecyclerView"),
                self._d(className="android.widget.ListView"),
            ]:
                if not rv_sel.exists:
                    continue
                card = rv_sel.child(index=click_index)
                if card.exists:
                    click_target = self._find_focusable_child(card)
                    if click_target is not None:
                        click_target.click()
                    else:
                        card.click()
                    time.sleep(0.3)
                    return self._do_buy_in_detail()
            self._log_step("WARNING", "未找到列表项")
            return False, "未找到列表项"
        except Exception as e:
            self._log_step("ERROR", "点击/下单异常: %s", str(e))
            return False, str(e)

    def _click_express_payment_if_any(self) -> None:
        """确认购买/免密支付后若出现「极速支付」或「极速付款」弹框，点击完成支付确认"""
        time.sleep(0.6)
        for btn in [
            self._d(textContains="极速支付"),
            self._d(textContains="极速付款"),
            self._d(descriptionContains="极速支付"),
            self._d(descriptionContains="极速付款"),
        ]:
            if btn.exists:
                self._log_step("INFO", "找到极速支付弹框，点击")
                btn.click()
                time.sleep(0.3)
                return
        try:
            w, h = self._d.window_size()
            self._log_step("INFO", "极速支付 selector 未找到，按弹框位置兜底点击底部 (%s, %s)", w // 2, h - 80)
            self._d.click(w // 2, h - 80)
            time.sleep(0.3)
        except Exception:
            pass

    def _is_fingerprint_prompt_visible(self) -> bool:
        """当前界面是否显示指纹/验证身份相关提示（需用户手动录入）"""
        try:
            for sel in [
                self._d(textContains="指纹"),
                self._d(descriptionContains="指纹"),
                self._d(textContains="验证身份"),
                self._d(descriptionContains="验证身份"),
                self._d(textContains="请录入"),
                self._d(descriptionContains="请录入"),
                self._d(textContains="指纹验证"),
                self._d(descriptionContains="指纹验证"),
            ]:
                if sel.exists:
                    return True
            return False
        except Exception:
            return False

    def _wait_fingerprint_if_any(self, timeout_sec: float = 15.0) -> None:
        """
        若出现指纹验证界面，等待用户手动录入指纹（轮询至界面消失或超时）。
        超时或用户回退后仅打日志，不抛异常，由调用方正常返回。
        """
        if not self._is_fingerprint_prompt_visible():
            return
        self._log_step("INFO", "检测到指纹验证，请在设备上完成录入（最多等待 %.0f 秒）", timeout_sec)
        step = 1.0
        elapsed = 0.0
        while elapsed < timeout_sec:
            time.sleep(step)
            elapsed += step
            if not self._is_fingerprint_prompt_visible():
                self._log_step("INFO", "指纹验证已通过或已关闭")
                return
        self._log_step("WARNING", "指纹等待超时，请手动完成验证或回退")

    def _do_buy_in_detail(self) -> Tuple[bool, str]:
        """在详情页点击「立即购买」/「我想要」，再处理弹框「免密支付」「确认购买」，若有「极速支付」再点"""
        try:
            self._log_step("INFO", "在详情页查找「立即购买」/「我想要」")
            buy_btn = self._d(text="立即购买") or self._d(text="我想要") or self._d(description="立即购买")
            if not buy_btn.exists:
                self._log_step("WARNING", "未找到购买按钮")
                return True, "已进入详情，未找到购买按钮"
            buy_btn.click()
            time.sleep(0.3)
            self._log_step("INFO", "已点击购买按钮，查找弹框「免密支付」/「确认购买」")
            self._dump_ui_if_debug("09_before_confirm_btn")  # 便于定位确认按钮未找到时的页面结构
            # 弹框：文案包含「免密支付」或「确认购买」的按钮，点其一即完成下单确认
            candidates = [
                ("textContains=免密支付", self._d(textContains="免密支付")),
                ("textContains=确认购买", self._d(textContains="确认购买")),
                ("descriptionContains=免密支付", self._d(descriptionContains="免密支付")),
                ("descriptionContains=确认购买", self._d(descriptionContains="确认购买")),
            ]
            for name, btn in candidates:
                self._log_step("INFO", "尝试 selector %s exists=%s", name, btn.exists)
                if btn.exists:
                    try:
                        raw = getattr(btn, "info", None)
                        info = (raw() if callable(raw) else raw) or {}
                        self._log_step("INFO", "找到按钮 %s text=%s description=%s bounds=%s",
                                       name, info.get("text"), info.get("contentDescription"), info.get("bounds"))
                    except Exception:
                        pass
                    btn.click()
                    time.sleep(0.3)
                    self._log_step("INFO", "已点击弹框（免密支付/确认购买）")
                    self._click_express_payment_if_any()
                    self._wait_fingerprint_if_any()
                    return True, "已点击购买并确认"
            # 弹框内「确认购买」常在独立层/WebView，dump 中无 TextView，用底部中心坐标兜底点击
            try:
                w, h = self._d.window_size()
                x, y = w // 2, h - 80
                self._log_step("INFO", "selector 未找到按钮，确认购买 按弹框位置兜底点击 (%s, %s)", x, y)
                self._d.click(x, y)
                time.sleep(0.3)
                self._log_step("INFO", "已执行兜底点击（确认购买常见位置）")
                self._click_express_payment_if_any()
                self._wait_fingerprint_if_any()
                return True, "已点击购买并确认"
            except Exception as e:
                self._log_step("WARNING", "兜底坐标点击失败: %s", e)
            self._log_step("INFO", "未发现免密支付/确认购买弹框，可能已跳过或需手动确认（可开启 DEBUG_DUMP_UI 查看 09_before_confirm_btn 页面快照）")
            return True, "已点击购买"
        except Exception as e:
            self._log_step("ERROR", "详情页购买异常: %s", str(e))
            return True, "已进入详情，" + str(e)

    def back_to_search_list(self) -> None:
        """从详情返回搜索结果列表"""
        try:
            self._log_step("INFO", "返回搜索结果列表")
            self._d.press("back")
            time.sleep(0.8)
            self._d.press("back")
            time.sleep(0.5)
        except Exception as e:
            self._log_step("WARNING", "返回列表异常: %s", str(e))

    def set_current_keyword(self, keyword: Optional[str]) -> None:
        """设置当前任务关键词，供刷新跑偏时重新走「首页→搜索→新发」恢复"""
        self._current_keyword = keyword

    def _has_text_or_desc(self, *names: str) -> bool:
        """界面是否存在任意一个 name 的 text 或 description 控件（用于页面类型判断）"""
        try:
            for name in names:
                for sel in [self._d(text=name), self._d(description=name)]:
                    if sel.exists:
                        return True
            return False
        except Exception:
            return False

    def is_on_home_new_tab(self) -> bool:
        """
        是否在主页的「新发」tab（关注/推荐/新发/省钱神券 等）。
        与搜索结果的「新发」区分：主页无「降价」「综合」「区域」「筛选」。
        """
        try:
            if not self._has_text_or_desc("新发"):
                return False
            if self._has_text_or_desc("降价"):
                return False
            if self._has_text_or_desc("综合", "区域", "筛选"):
                return False
            return self._has_text_or_desc("关注", "推荐", "省钱神券", "找服务", "热点")
        except Exception:
            return False

    def is_on_new_drop_page(self) -> bool:
        """
        当前是否在搜索结果列表页（综合/新发/降价/价格 等 tab 所在页）。
        必须存在「降价」才视为搜索页，避免与主页「新发」tab 混淆；且存在「新发」「综合」「价格」之一（点击价格后当前 tab 为价格，仍需判为搜索页）。
        """
        try:
            if not self._has_text_or_desc("降价"):
                return False
            return self._has_text_or_desc("新发", "综合", "价格")
        except Exception:
            return False

    def _dismiss_popup_if_any(self) -> None:
        """尝试关闭可能存在的弹框（随机点击触发的提示等），避免被误判为不在搜索页。优先点关闭/取消等，再按 back"""
        for text in ("关闭", "取消", "知道了", "稍后", "确定"):
            try:
                for sel in [
                    self._d(text=text),
                    self._d(textContains=text),
                    self._d(description=text),
                    self._d(descriptionContains=text),
                ]:
                    if sel.exists:
                        sel.click()
                        time.sleep(0.4)
                        return
            except Exception:
                pass
        try:
            self._d.press("back")
            time.sleep(0.35)
        except Exception:
            pass

    def _ensure_on_search_list(self) -> bool:
        """若当前不在搜索结果列表页，优先直接找搜索框搜关键词；失败再返回或回首页完整恢复。返回是否已在列表页"""
        if self.is_on_new_drop_page():
            return True
        time.sleep(0.4)
        if self.is_on_new_drop_page():
            return True
        # 再等一档，避免 tab 点击后列表页尚未渲染完就误判
        time.sleep(0.6)
        if self.is_on_new_drop_page():
            return True
        if self._current_keyword:
            self._log_step("INFO", "检测到非搜索页，直接查找关键字录入框并搜索")
            if self._search_from_current_page(self._current_keyword):
                return True
        if self.is_on_home_new_tab():
            self._log_step("WARNING", "直接搜索未成功且处于主页「新发」tab，回首页后重新搜索")
            if self._current_keyword:
                self.go_home()
                time.sleep(2)
                if self.ensure_home_then_search(self._current_keyword):
                    return True
            return False
        # 可能是弹框遮挡导致误判：先尝试关闭弹框（返回键或关闭/取消/知道了）再判一次
        self._dismiss_popup_if_any()
        time.sleep(0.5)
        if self.is_on_new_drop_page():
            return True
        # 确认为非列表页后再返回，减少「点击 tab 后加载稍慢」导致的误报
        self._log_step("WARNING", "当前不在列表页，尝试返回")
        self.back_to_search_list()
        time.sleep(1)
        if self.is_on_new_drop_page():
            return True
        if self._current_keyword:
            if self._search_from_current_page(self._current_keyword):
                return True
            self._log_step("INFO", "返回后直接搜索仍未到列表，按首页→搜索→新发 重新定位")
            for _ in range(4):
                self._d.press("back")
                time.sleep(0.5)
            self.go_home()
            time.sleep(2)
            if self.ensure_home_then_search(self._current_keyword):
                return True
        return False

    def _is_tab_bar_bounds(self, bounds, window_height: int) -> bool:
        """判断 bounds 是否在屏幕顶部 tab 栏区域（避免点到列表里的「新发」等文案）；顶部 35% 内视为 tab 栏"""
        try:
            if isinstance(bounds, dict):
                top = int(bounds.get("top", 9999))
            else:
                parts = re.findall(r"\d+", str(bounds))
                if len(parts) >= 4:
                    top = int(parts[1])
                else:
                    return True
            return top < 0.35 * window_height
        except Exception:
            return True

    def _click_tab(self, name: str) -> bool:
        """点击指定 tab（新发/降价/价格）；仅当已在列表页且优先点顶部 tab 栏内控件，点后校验并必要时恢复"""
        if not self.is_on_new_drop_page():
            self._ensure_on_search_list()
            if not self.is_on_new_drop_page():
                return False
        try:
            w, h = self._d.window_size()
        except Exception:
            w, h = 1080, 2400
        # 多种匹配方式：精确 + 包含，避免文案带空格等未命中
        selectors = [
            self._d(text=name),
            self._d(description=name),
            self._d(textContains=name),
            self._d(descriptionContains=name),
        ]
        for sel in selectors:
            if not sel.exists:
                continue
            try:
                info = sel.info
                bounds = info.get("bounds") or info.get("visibleBounds")
                if bounds and not self._is_tab_bar_bounds(bounds, h):
                    continue
            except Exception:
                pass
            sel.click()
            time.sleep(1.2)
            self._current_tab = name
            self._ensure_on_search_list()
            return True
        # 未找到顶部 tab 时兜底：任意匹配点一次
        for sel in selectors:
            if sel.exists:
                sel.click()
                time.sleep(1.2)
                self._current_tab = name
                self._ensure_on_search_list()
                return True
        return False

    def _simulate_page_scroll(self) -> None:
        """在页面上轻微上下滑动；步数与坐标随机，间隔随机几秒"""
        try:
            steps = random.randint(25, 38)
            cx = random.uniform(0.48, 0.52)
            y1 = 0.55 + random.uniform(-0.02, 0.02)
            y2 = 0.35 + random.uniform(-0.02, 0.02)
            self._d.swipe(cx, y1, cx, y2, steps=steps)
            time.sleep(random.uniform(0.25, 0.5))
            cx2 = random.uniform(0.48, 0.52)
            self._d.swipe(cx2, y2, cx2, y1, steps=random.randint(22, 35))
            time.sleep(random.uniform(0.15, 0.35))
        except Exception as e:
            if self._log:
                self._log("WARNING", "模拟滑动异常: %s" % e)

    # 轮询点击顺序：仅新发/降价/价格，不点「综合」
    _REFRESH_TAB_ORDER = ("新发", "降价", "新发", "价格")

    def refresh_search_list(self, keyword: Optional[str] = None) -> None:
        """按 新发→降价→新发→价格 轮询点击（不点综合）；跑偏时用 keyword 重新定位。传入 keyword 会更新 _current_keyword"""
        if keyword is not None:
            self._current_keyword = keyword
        try:
            if not self._ensure_on_search_list():
                self._log_step("WARNING", "刷新前未在列表页且恢复失败，跳过本次刷新")
                return
            self._log_step("INFO", "刷新：新发→降价→新发→价格（不点综合）")
            for i, tab_name in enumerate(self._REFRESH_TAB_ORDER):
                self._click_tab(tab_name)
                if tab_name == "降价":
                    time.sleep(random.uniform(0.8, 1.3))
                    self._simulate_page_scroll()
                elif tab_name == "新发" and i == 2:
                    time.sleep(random.uniform(1.5, 2.5))
                else:
                    time.sleep(random.uniform(0.8, 1.3))
            # 点击价格后多等一会再判页，避免列表页尚未渲染完就误判「当前不在列表页」
            time.sleep(1.0)
            self._ensure_on_search_list()
            # 刷新结束停在「价格」tab，本页不解析商品
            self._current_tab = "价格"
        except Exception as e:
            self._log_step("WARNING", "刷新 tab 异常: %s", str(e))
            try:
                self._ensure_on_search_list()
            except Exception:
                pass

    def is_on_price_tab(self) -> bool:
        """当前是否在「价格」tab；价格页不解析商品，仅新发/降价页解析"""
        return getattr(self, "_current_tab", None) == "价格"

    def should_parse_items(self) -> bool:
        """是否需要解析商品：新发、降价需要解析，价格页不解析"""
        return not self.is_on_price_tab()
