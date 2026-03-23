# -*- coding: utf-8 -*-
"""多手机接入：通过 ADB 发现设备，u2 连接控制。adbutils/u2 均延迟导入，避免启动时 pkg_resources 导致服务起不来"""

from typing import Optional, List, Dict


def list_adb_devices() -> List[Dict[str, str]]:
    """列出当前 USB 连接的 Android 设备（serial + 型号等）"""
    try:
        import adbutils
    except ModuleNotFoundError as e:
        if "pkg_resources" in str(e):
            raise RuntimeError(
                "缺少 pkg_resources，请在本机执行: .venv/bin/pip install --force-reinstall setuptools"
            ) from e
        raise
    adb = adbutils.AdbClient()
    result = []
    for d in adb.device_list():
        info = {"serial": d.serial, "prop": {}}
        try:
            info["prop"] = {
                "model": d.prop.get("ro.product.model", ""),
                "device": d.prop.get("ro.product.device", ""),
            }
        except Exception:
            pass
        result.append(info)
    return result


def connect_device(serial: str):
    """根据 serial 连接一台设备，失败返回 None。返回类型为 u2.Device 或 None。"""
    try:
        import uiautomator2 as u2
        return u2.connect(serial)
    except ModuleNotFoundError as e:
        if "pkg_resources" in str(e):
            raise RuntimeError(
                "缺少 pkg_resources，请在本机执行: .venv/bin/pip install --force-reinstall setuptools"
            ) from e
        raise
    except Exception:
        return None


def get_device_serial_list() -> List[str]:
    """仅返回 device_id 列表，供后台展示与绑定"""
    return [d["serial"] for d in list_adb_devices()]
