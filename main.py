# -*- coding: utf-8 -*-
"""进程入口：启动后台服务。需先安装依赖并连接手机。"""

import sys
import os

# 强制使用项目 .venv 的 site-packages（解决 venv 为系统 Python 符号链接时找不到 pkg_resources）
_py_ver = "python%s.%s" % (sys.version_info.major, sys.version_info.minor)
if os.environ.get("VIRTUAL_ENV"):
    _site = os.path.join(os.environ["VIRTUAL_ENV"], "lib", _py_ver, "site-packages")
else:
    _root = os.path.dirname(os.path.abspath(__file__))
    _site = os.path.join(_root, ".venv", "lib", _py_ver, "site-packages")
if os.path.isdir(_site) and _site not in sys.path:
    sys.path.insert(0, _site)

# 启动时校验 pkg_resources，缺则直接退出并提示用 .venv/bin/pip 安装
try:
    import pkg_resources  # noqa: F401
except ModuleNotFoundError:
    print("错误: 当前环境找不到 pkg_resources（setuptools 未装入本项目的 .venv）")
    print("已尝试路径: %s" % _site)
    print("请在本机执行：")
    print("  cd <项目目录>")
    print("  .venv/bin/pip install --force-reinstall setuptools")
    print("  sh ./run.sh")
    sys.exit(1)

try:
    import uvicorn
except ModuleNotFoundError:
    print("未安装依赖，请先执行：")
    print("  python3 -m venv .venv")
    print("  source .venv/bin/activate   # Windows: .venv\\Scripts\\activate")
    print("  pip install -r requirements.txt")
    sys.exit(1)

try:
    from config import settings
except Exception as e:
    print(f"加载配置失败: {e}")
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
            print(f"端口 {settings.port} 已被占用，请更换端口或关闭占用进程")
        else:
            print(f"启动失败(OSError): {e}")
        sys.exit(1)
    except ModuleNotFoundError as e:
        if "pkg_resources" in str(e):
            print("缺少 pkg_resources，请先执行：pip install setuptools")
        else:
            print(f"缺少模块: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"启动失败: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
