# -*- coding: utf-8 -*-
"""进程入口：启动后台服务。需先安装依赖并连接手机。"""

import glob
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_PY_TAG = "python%d.%d" % (sys.version_info.major, sys.version_info.minor)


def _resolve_site_packages() -> tuple[str | None, str]:
    """
    解析应加入 sys.path 的 site-packages，并返回 (路径或 None, 说明文案)。
    禁止把「另一 Python 版本」的 site-packages 塞进当前解释器，否则易表现为已 pip 仍 import 失败。
    """
    if os.environ.get("VIRTUAL_ENV"):
        base = os.environ["VIRTUAL_ENV"]
        sp = os.path.join(base, "lib", _PY_TAG, "site-packages")
        if os.path.isdir(sp):
            return sp, "已激活 venv: %s" % base
        return None, "已设置 VIRTUAL_ENV 但缺少 %s（请检查 venv 是否损坏）" % sp

    venv_lib = os.path.join(_PROJECT_ROOT, ".venv", "lib")
    sp = os.path.join(venv_lib, _PY_TAG, "site-packages")
    if os.path.isdir(sp):
        return sp, "项目 .venv 与当前解释器一致 (%s)" % _PY_TAG

    if not os.path.isdir(venv_lib):
        return None, "未找到 %s，请先: python3 -m venv .venv" % os.path.join(_PROJECT_ROOT, ".venv")

    found = sorted(glob.glob(os.path.join(venv_lib, "python*", "site-packages")))
    found = [p for p in found if os.path.isdir(p)]
    tags = [os.path.basename(os.path.dirname(p)) for p in found]
    if tags:
        return (
            None,
            "当前解释器是 %s，但依赖装在 .venv 的 %s 下。请用: %s/.venv/bin/python main.py（勿用系统 python 直接跑）"
            % (_PY_TAG, ",".join(tags), _PROJECT_ROOT),
        )
    return None, ".venv/lib 下无 site-packages，请执行: .venv/bin/python -m pip install -r requirements.txt"


_site, _site_hint = _resolve_site_packages()
if _site and _site not in sys.path:
    sys.path.insert(0, _site)

# 启动时校验 pkg_resources（adbutils/u2 依赖；setuptools>=82 已移除，见 requirements.txt）
try:
    import pkg_resources  # noqa: F401
except ModuleNotFoundError:
    print("错误: 找不到 pkg_resources（%s）" % _site_hint)
    if _site:
        print("已加入/期望路径: %s （存在: %s）" % (_site, os.path.isdir(_site)))
    else:
        print("未能解析 site-packages，请确认使用: %s/.venv/bin/python main.py" % _PROJECT_ROOT)
    print("请执行（务必用本项目的 pip）：")
    print("  cd \"%s\"" % _PROJECT_ROOT)
    print("  .venv/bin/python -m pip install -r requirements.txt")
    print("  .venv/bin/python -m pip install \"setuptools>=65,<82\"")
    sys.exit(1)

try:
    import uvicorn
except (ModuleNotFoundError, ImportError) as e:
    print("无法 import uvicorn: %s" % e)
    print("当前解释器: %s" % sys.executable)
    print("Python 版本: %s | 期望 site-packages: %s" % (sys.version.split()[0], _site or "(未解析)"))
    print("%s" % _site_hint)
    print("请在本项目根目录执行（不要用系统 pip 装到别的环境）：")
    print("  cd \"%s\"" % _PROJECT_ROOT)
    print("  %s -m venv .venv" % (sys.executable if "venv" not in sys.executable else "python3"))
    print("  .venv/bin/python -m pip install -U pip")
    print("  .venv/bin/python -m pip install -r requirements.txt")
    print("启动请用: .venv/bin/python main.py   或   ./run.sh")
    sys.exit(1)

try:
    from config import settings
except Exception as e:
    print("加载配置失败: %s" % e)
    print("当前解释器: %s" % sys.executable)
    sys.exit(1)


def main():
    try:
        uvicorn.run(
            "app.main:app",
            host=settings.host,
            port=settings.port,
            reload=False,
        )
    except KeyboardInterrupt:
        print("\n已退出")
        sys.exit(0)
    except OSError as e:
        if "Address already in use" in str(e) or e.errno == 48:
            print("端口 %s 已被占用，请更换端口或关闭占用进程" % settings.port)
        else:
            print("启动失败(OSError): %s" % e)
        sys.exit(1)
    except ModuleNotFoundError as e:
        if "pkg_resources" in str(e):
            print("缺少 pkg_resources，请先执行：.venv/bin/python -m pip install \"setuptools>=65,<82\"")
        else:
            print("缺少模块: %s" % e)
        sys.exit(1)
    except Exception as e:
        print("启动失败: %s: %s" % (type(e).__name__, e))
        sys.exit(1)


if __name__ == "__main__":
    main()
