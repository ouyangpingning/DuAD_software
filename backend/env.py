"""backend 环境辅助：确保 LD_LIBRARY_PATH 含 backend/libs（相机 SDK）。

main.py 已内联此逻辑；诊断/测试脚本 import 本模块即可复用。
glibc 的 dlopen 依赖解析只认进程启动时的 ld 路径，运行时设置无效，
因此缺失时必须重启进程。
"""
import os
import sys
from pathlib import Path

_LIBS_DIR = Path(__file__).resolve().parent / "libs"


def ensure_sdk_paths():
    if not _LIBS_DIR.is_dir():
        return
    libs_env = str(_LIBS_DIR)
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    if libs_env not in ld_path.split(":"):
        os.environ["LD_LIBRARY_PATH"] = libs_env + ((":" + ld_path) if ld_path else "")
        print(f"[env] 注入 LD_LIBRARY_PATH（{libs_env}）并重启进程")
        os.execv(sys.executable, [sys.executable, "-u"] + sys.argv)
