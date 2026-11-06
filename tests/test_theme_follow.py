#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主题「跟随系统」回归测试。

覆盖四件事（都是 2026-11 新功能）：
  1. Colors 单例：system 模式按 systemDark 出亮/暗；用户明确选 light/dark 时
     系统变化不能影响界面。
  2. AppBridge：themeMode / preset 持久化（注入临时 QSettings），非法值拒绝。
  3. AppBridge 监听系统亮/暗信号：系统切暗时 systemDark 与 Colors 同步变化，
     且只影响 system 模式。
  4. 启动检测：Linux 优先 xdg-desktop-portal 的 color-scheme（docs/19 §40）
     —— 修复"关着软件时切成暗色、再启动还是亮色"。

用法：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 -u tests/test_theme_follow.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT", "1")

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"

# ⚠ 与 render_pages 一样：必须在 import main 之前把 backend/libs 放进
#   LD_LIBRARY_PATH，否则 main.py 会 execv 重启进程（测试会“凭空重来”）。
_libs = ROOT / "backend" / "libs"
if _libs.is_dir() and str(_libs) not in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
    _old = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = str(_libs) + ((":" + _old) if _old else "")

sys.path.insert(0, str(CONTENT))          # main.AppBridge

from PySide6.QtCore import QSettings                        # noqa: E402
from PySide6.QtGui import QGuiApplication                  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine            # noqa: E402

from main import AppBridge                                 # noqa: E402


class Runner:
    def __init__(self):
        self.fails = []

    def check(self, cond, msg):
        print(("  ✓ " if cond else "  ✗ ") + msg)
        if not cond:
            self.fails.append(msg)

    def summary(self):
        print()
        if self.fails:
            print(f"主题跟随系统测试 失败 {len(self.fails)} 条：")
            for f in self.fails:
                print("  ✗ " + f)
            return 1
        print("主题跟随系统测试 全部通过")
        return 0


def new_engine():
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(CONTENT))
    return engine


def new_bridge(app, engine, theme=None, preset=None):
    sdir = Path(tempfile.mkdtemp(prefix="duad_theme_"))
    sfile = sdir / "app.ini"
    settings = QSettings(str(sfile), QSettings.Format.IniFormat)
    if theme is not None:
        settings.setValue("theme", theme)
    if preset is not None:
        settings.setValue("preset", preset)
    bridge = AppBridge(app, engine, settings=settings)
    return bridge, settings, sfile


def colors_of(engine):
    colors = engine.singletonInstance("DuAD_Software", "Colors")
    colors.setProperty("animDuration", 0)   # 测试只关心终值
    return colors


def test_colors_follow_system(app, r):
    print("\n=== 1. Colors：system / light / dark ===")
    engine = new_engine()
    colors = colors_of(engine)

    colors.setTheme("light")
    colors.setProperty("systemDark", True)
    r.check(colors.property("pageBg").name() == "#ffffff",
            "显式 light：系统变暗也不改颜色")

    colors.setTheme("set-system")            # 先确认非法值不会污染状态
    r.check(colors.property("pageBg").name() == "#ffffff",
            "setTheme 拒绝未知模式")

    colors.setTheme("system")
    r.check(colors.property("pageBg").name() == "#1e1e1e",
            "system + systemDark=true → 暗色")

    colors.setProperty("systemDark", False)
    r.check(colors.property("pageBg").name() == "#ffffff",
            "system + systemDark=false → 亮色")

    colors.setTheme("dark")
    colors.setProperty("systemDark", False)
    r.check(colors.property("pageBg").name() == "#1e1e1e",
            "显式 dark：系统变亮也不改颜色")


def test_bridge_persistence(app, r):
    print("\n=== 2. AppBridge：默认值 / 持久化 / 非法值 ===")
    engine = new_engine()
    bridge, settings, sfile = new_bridge(app, engine)
    colors = colors_of(engine)

    r.check(bridge.themeMode == "system", "默认主题 = system")
    r.check(bridge.preset == "default", "默认配色 = default")

    bridge.themeMode = "dark"
    bridge.preset = "ocean"
    settings.sync()

    saved = QSettings(str(sfile), QSettings.Format.IniFormat)
    r.check(saved.value("theme") == "dark", "theme 写入 QSettings")
    r.check(saved.value("preset") == "ocean", "preset 写入 QSettings")
    r.check(colors.property("pageBg").name() == "#1e1e1e",
            "切 dark 后 Colors 立即变暗")
    r.check(colors.property("accent").name().upper() == "#6AB0E0".upper(),
            "切 ocean 后 Colors 强调色为暗色版海洋蓝")

    # 新桥读同一份配置：模拟重启
    bridge2 = AppBridge(app, engine, settings=QSettings(str(sfile),
                                                        QSettings.Format.IniFormat))
    r.check(bridge2.themeMode == "dark", "重启恢复 theme")
    r.check(bridge2.preset == "ocean", "重启恢复 preset")
    bridge2 = None  # noqa: F841

    bridge.themeMode = "neon"
    r.check(bridge.themeMode == "dark", "非法主题被拒绝，保持原值")
    bridge.preset = "rainbow"
    r.check(bridge.preset == "ocean", "非法配色被拒绝，保持原值")


def test_live_system_change(app, r):
    print("\n=== 3. 系统亮/暗实时切换（模拟 colorSchemeChanged）===")
    # ⚠ offscreen 平台下 QStyleHints.colorScheme() 恒为 Unknown，
    #   setColorScheme() 也不生效，不能靠它模拟系统切换。
    #   这里直接替换检测函数后调用同一个回调 —— 正是 colorSchemeChanged
    #   连接到的槽，测的是"检测值 → systemDark → Colors"这条链路。
    engine = new_engine()
    bridge, settings, _ = new_bridge(app, engine, theme="system")
    colors = colors_of(engine)

    bridge._detect_system_dark = lambda: False
    bridge._on_system_theme_changed()
    app.processEvents()
    r.check(bridge.systemDark is False, "检测到系统亮色 → systemDark=false")
    r.check(colors.property("pageBg").name() == "#ffffff", "system 模式为亮色")

    changes = []
    bridge.systemDarkChanged.connect(lambda: changes.append(bridge.systemDark))
    bridge._detect_system_dark = lambda: True
    bridge._on_system_theme_changed()
    app.processEvents()
    r.check(bridge.systemDark is True, "检测到系统暗色 → systemDark=true")
    r.check(changes == [True], "systemDarkChanged 只发一次")
    r.check(colors.property("pageBg").name() == "#1e1e1e",
            "system 模式自动跟随为暗色")

    # 用户明确选亮色后，系统再变暗也不应影响界面
    bridge.themeMode = "light"
    r.check(colors.property("pageBg").name() == "#ffffff", "切到显式 light")
    bridge._detect_system_dark = lambda: False
    bridge._on_system_theme_changed()          # 先回亮色，让下一次变化能触发
    bridge._detect_system_dark = lambda: True
    bridge._on_system_theme_changed()          # 再变暗
    app.processEvents()
    r.check(bridge.systemDark is True, "systemDark 仍在更新（供切回时使用）")
    r.check(colors.property("pageBg").name() == "#ffffff",
            "显式 light 不受系统变暗影响")

    # 切回 system：立即采用最新的系统值
    bridge.themeMode = "system"
    r.check(colors.property("pageBg").name() == "#1e1e1e",
            "切回 system 立刻采用当前系统暗色")


class _FakeReply:
    """假 D-Bus 回复：只需要 type() 与 arguments()。"""

    def __init__(self, value):
        self._value = value

    def type(self):
        from PySide6.QtDBus import QDBusMessage
        return QDBusMessage.MessageType.ReplyMessage

    def arguments(self):
        return [self._value]


class _FakePortal:
    """假 portal 接口：Read("…appearance","color-scheme") 固定返回一个值。"""

    def __init__(self, value):
        self._value = value

    def call(self, *_args):
        return _FakeReply(self._value)


def test_portal_detection(app, r):
    print("\n=== 4. 启动检测：portal 优先（关着软件时切主题也要生效）===")
    engine = new_engine()
    bridge, settings, _ = new_bridge(app, engine, theme="system")

    # 4a. color-scheme 取值映射：1=暗 / 2=亮 / 0=无偏好（交给后面兜底）
    bridge._portal_tried = True
    bridge._portal = _FakePortal(1)
    r.check(bridge._detect_system_dark() is True,
            "portal color-scheme=1（偏好暗色）→ 暗色")
    bridge._portal = _FakePortal(2)
    r.check(bridge._detect_system_dark() is False,
            "portal color-scheme=2（偏好亮色）→ 亮色")
    bridge._portal = _FakePortal(0)
    # offscreen 平台下 QStyleHints 恒为 Unknown、默认调色板是亮色
    r.check(bridge._detect_system_dark() is False,
            "portal 无偏好（0）→ 退回 Qt / 调色板（offscreen 为亮色）")

    # 4b. 真正的 bug 场景：portal 说暗、Qt 的 colorScheme() 说亮（GNOME 实测），
    #     启动首帧就必须是暗色，不能"先亮后暗"。
    engine2 = new_engine()
    colors2 = colors_of(engine2)
    orig = AppBridge._portal_color_scheme
    AppBridge._portal_color_scheme = lambda self: True
    try:
        bridge2, _, _ = new_bridge(app, engine2, theme="system")
    finally:
        AppBridge._portal_color_scheme = orig
    app.processEvents()
    r.check(bridge2.systemDark is True,
            "启动即检测到系统暗色（不依赖 Qt 的 colorScheme）")
    r.check(colors2.property("pageBg").name() == "#1e1e1e",
            "首帧就是暗色（不会白闪一下）")

    # 4c. portal 不可用（无 D-Bus / 非 Linux）时不能崩，走旧兜底
    bridge2._portal_tried = True
    bridge2._portal = None
    r.check(isinstance(bridge2._detect_system_dark(), bool),
            "portal 不可用时退回调色板检测，仍返回布尔值")


def main():
    app = QGuiApplication(sys.argv[:1])
    r = Runner()

    test_colors_follow_system(app, r)
    test_bridge_persistence(app, r)
    test_live_system_change(app, r)
    test_portal_detection(app, r)

    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
