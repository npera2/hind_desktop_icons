"""开机自启开关（写 HKCU\\...\\Run，不需要管理员权限）。

开机自启时使用 ``--no-start-toggle``：只启动托盘常驻、不去动桌面图标，
避免"每次开机图标都被藏起来"这种惊吓。真正想隐藏时再点一次即可。
"""

from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "HindDesktopIcons"
AUTOSTART_ARGS = "--no-start-toggle"


def _executable_command() -> str:
    """返回可直接写进注册表 Run 项的完整命令行（含引号）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {AUTOSTART_ARGS}'

    # 源码方式运行：换成 pythonw.exe，避免自启时闪出一个控制台窗口
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else python
    return f'"{interpreter}" -m hind_desktop_icons {AUTOSTART_ARGS}'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("读取开机自启项失败：%s", exc)
        return False


def enable() -> bool:
    command = _executable_command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        log.error("写入开机自启项失败：%s", exc)
        return False
    log.info("已开启开机自启：%s", command)
    return True


def disable() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.error("删除开机自启项失败：%s", exc)
        return False
    log.info("已关闭开机自启")
    return True


def set_enabled(enabled: bool) -> bool:
    return enable() if enabled else disable()
