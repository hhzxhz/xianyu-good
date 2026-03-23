# -*- coding: utf-8 -*-
"""逐步调试闲鱼流程：连接设备后按步骤执行并打印，便于定位问题。
用法：.venv/bin/python debug_steps.py [设备serial] [搜索关键字]
环境变量 FILL_DEBUG=1 时会把各步骤的 hierarchy 保存到 logs/fill_debug_*.xml"""

import sys
import os
import time

# 与 main.py 一致，保证能加载 .venv 与项目模块
_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(_root)
_py_ver = "python%s.%s" % (sys.version_info.major, sys.version_info.minor)
_site = os.path.join(_root, ".venv", "lib", _py_ver, "site-packages")
if os.path.isdir(_site) and _site not in sys.path:
    sys.path.insert(0, _site)

def _log(step: str, msg: str, *args):
    text = msg % args if args else msg
    print("[%s] %s" % (step, text))

def main():
    from app.core.device import list_adb_devices, connect_device, get_device_serial_list
    from app.core.xianyu import XianyuDriver

    serial = None
    keyword = "测试"
    if len(sys.argv) >= 2:
        serial = sys.argv[1]
    if len(sys.argv) >= 3:
        keyword = sys.argv[2]

    _log("INIT", "列出设备…")
    try:
        devices = list_adb_devices()
    except Exception as e:
        _log("ERROR", "列出设备失败: %s", e)
        return 1
    if not devices:
        _log("ERROR", "未发现 USB 设备，请连接手机并开启 USB 调试")
        return 1
    serials = get_device_serial_list()
    _log("INIT", "已连接设备: %s", serials)
    if not serial:
        serial = serials[0]
        _log("INIT", "使用第一台设备: %s", serial)
    else:
        if serial not in serials:
            _log("ERROR", "设备 %s 不在列表中: %s", serial, serials)
            return 1

    _log("STEP", "连接 u2…")
    device = connect_device(serial)
    if not device:
        _log("ERROR", "连接设备失败: %s", serial)
        return 1
    _log("STEP", "连接成功")

    def log_cb(level: str, msg: str):
        print("  [%s] %s" % (level, msg))

    # FILL_DEBUG=1 时把 hierarchy 保存到 logs/ 便于排查
    on_dump = None
    if os.environ.get("FILL_DEBUG") == "1":
        log_dir = os.path.join(_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        prefix = time.strftime("fill_debug_%H%M%S")

        def on_dump(step_name: str, xml: str):
            path = os.path.join(log_dir, "%s_%s.xml" % (prefix, step_name.replace(" ", "_")))
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(xml or "")
                _log("DUMP", "hierarchy 已保存: %s", path)
            except Exception as e:
                _log("WARN", "保存 hierarchy 失败: %s", e)

    driver = XianyuDriver(device, log_cb=log_cb, on_dump=on_dump)

    # 1. 检查安装
    _log("1/4", "检查是否安装闲鱼…")
    if not driver.is_app_installed():
        _log("FAIL", "未安装闲鱼，请先安装 App")
        return 1
    _log("OK", "已安装闲鱼")

    # 2. 检查启动
    _log("2/4", "检查闲鱼是否在前台…")
    if not driver.is_app_running():
        _log("STEP", "未在前台，启动闲鱼…")
        if not driver.start_app():
            _log("FAIL", "启动闲鱼失败")
            return 1
        time.sleep(2)
    _log("OK", "闲鱼已在前台")

    # 3. 检查登录
    _log("3/4", "检查是否已登录…")
    if not driver.is_logged_in():
        _log("FAIL", "未检测到登录状态，请先在设备上登录闲鱼")
        return 1
    _log("OK", "已登录")

    # 4. 定位首页并搜索
    _log("4/4", "定位到首页并搜索关键字: %s", keyword)
    if not driver.ensure_home_then_search(keyword):
        _log("FAIL", "搜索失败")
        return 1
    _log("OK", "搜索流程结束")
    return 0

if __name__ == "__main__":
    sys.exit(main())
