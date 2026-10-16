"""桌面图标显隐控制核心库。

原理
----
桌面图标不是独立程序绘制的，而是 explorer.exe 内部的一个 ListView 控件：

    Progman / WorkerW            <- 桌面宿主窗口
      └─ SHELLDLL_DefView        <- 桌面视图容器，切换命令的接收者
           └─ SysListView32      <- 真正绘制图标的控件

把「显示桌面图标」切掉的方式有三种，按优先级降级：

    A. command    向 SHELLDLL_DefView 发送 WM_COMMAND = 0x7402
                  等价于桌面右键「查看 -> 显示桌面图标」，与系统菜单状态同步，
                  explorer 重启后状态延续。命令是"取反"语义，所以要先读状态再决定是否发送。
    B. showwindow 对 SysListView32 调 ShowWindow(SW_HIDE / SW_SHOW)
                  任何 Windows 版本都有效，但桌面右键菜单会失效、explorer 重启后会被重置。
    C. registry   写 HKCU\\...\\Explorer\\Advanced\\HideIcons（本库未自动使用，仅作诊断输出）

状态判定统一以 SysListView32 的 WS_VISIBLE 为准：实测 A、B 两种方式都会改变它，
因此一个判据同时覆盖两条路径。
"""

from __future__ import annotations

import ctypes
import logging
import time
import winreg
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

# 64 位下必须显式声明签名，否则窗口句柄会被按 int32 截断
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]

WM_COMMAND = 0x0111
CMD_TOGGLE_DESKTOP_ICONS = 0x7402
SW_HIDE = 0
SW_SHOW = 5

# 方案标识
METHOD_COMMAND = "command"
METHOD_SHOWWINDOW = "showwindow"

CLASS_PROGMAN = "Progman"
CLASS_DEFVIEW = "SHELLDLL_DefView"
CLASS_LISTVIEW = "SysListView32"

ADV_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
HIDE_VALUE = "HideIcons"

_VERIFY_TIMEOUT = 1.0  # 切换后等待系统生效的上限（秒）
_VERIFY_INTERVAL = 0.05


class DesktopIconsError(RuntimeError):
    """找不到桌面图标窗口，无法操作。"""


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def _find_child(parent: int, cls: str) -> int:
    return user32.FindWindowExW(wintypes.HWND(parent), wintypes.HWND(0), cls, None) or 0


def find_def_view() -> int:
    """返回 SHELLDLL_DefView 句柄；兼容 Progman 与 WorkerW 两种宿主。

    Windows 11 以及开启了动态壁纸（Wallpaper Engine / Lively）的环境下，
    SHELLDLL_DefView 可能被挂在某个 WorkerW 顶层窗口下，因此不能只查 Progman，
    必须遍历所有顶层窗口兜底。
    """
    progman = user32.FindWindowW(CLASS_PROGMAN, None) or 0
    if progman:
        dv = _find_child(progman, CLASS_DEFVIEW)
        if dv:
            return dv

    hits: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        hwnd = hwnd if isinstance(hwnd, int) else ctypes.cast(hwnd, ctypes.c_void_p).value
        dv = _find_child(hwnd, CLASS_DEFVIEW)
        if dv:
            hits.append(dv)
        return True

    user32.EnumWindows(enum_proc(_cb), 0)
    if hits:
        if len(hits) > 1:
            log.warning("发现 %d 个 SHELLDLL_DefView，使用第一个", len(hits))
        return hits[0]
    return 0


def find_list_view() -> int:
    """返回 SysListView32（图标控件本体）句柄，取不到返回 0。"""
    dv = find_def_view()
    return _find_child(dv, CLASS_LISTVIEW) if dv else 0


def registry_hidden() -> bool | None:
    """读系统开关 HideIcons。返回 None 表示键值不存在（等价于"显示中"）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ADV_KEY) as key:
            value, _ = winreg.QueryValueEx(key, HIDE_VALUE)
            return bool(value)
    except OSError:
        return None


class DesktopIcons:
    """桌面图标显隐控制器。线程安全性：单线程使用（托盘与 IPC 回调需自行串行化）。"""

    def __init__(self) -> None:
        self.method = METHOD_COMMAND

    # ---------- 状态查询 ----------

    def is_visible(self) -> bool:
        """以 SysListView32 的 WS_VISIBLE 为准判定图标是否可见。"""
        lv = find_list_view()
        if not lv:
            # 取不到控件时退回注册表口径
            return not bool(registry_hidden())
        return bool(user32.IsWindowVisible(wintypes.HWND(lv)))

    def info(self) -> dict:
        """诊断信息，供 --status 与「关于」使用。"""
        dv = find_def_view()
        lv = _find_child(dv, CLASS_LISTVIEW) if dv else 0
        parent = user32.GetParent(wintypes.HWND(dv)) if dv else 0
        return {
            "method": self.method,
            "visible": self.is_visible(),
            "hideicons": registry_hidden(),
            "defview": dv,
            "listview": lv,
            "defview_parent": parent,
            "defview_parent_class": _class_name(parent) if parent else "",
        }

    # ---------- 切换 ----------

    def set_visible(self, visible: bool, *, verify: bool = True) -> bool:
        """把桌面图标设为显示 / 隐藏。返回是否最终达成目标状态。"""
        try:
            if self.is_visible() == visible:
                return True
        except DesktopIconsError:
            pass

        ok = self._apply(visible)
        if ok and verify and self._wait_for(visible):
            return True

        if self.method == METHOD_COMMAND:
            log.warning("WM_COMMAND 方案未生效，降级为 ShowWindow")
            self.method = METHOD_SHOWWINDOW
            ok = self._apply(visible)
            if ok and self._wait_for(visible):
                return True

        log.error("切换桌面图标失败：目标 visible=%s，当前 visible=%s", visible, self.is_visible())
        return False

    def toggle(self) -> bool:
        """取反切换。始终以系统真实状态为准，不缓存自身状态。"""
        target = not self.is_visible()
        return self.set_visible(target)

    # ---------- 内部实现 ----------

    def _apply(self, visible: bool) -> bool:
        if self.method == METHOD_COMMAND:
            return self._send_toggle_command()
        return self._show_window(visible)

    def _send_toggle_command(self) -> bool:
        dv = find_def_view()
        if not dv:
            log.warning("未找到 SHELLDLL_DefView")
            return False
        user32.SendMessageW(
            wintypes.HWND(dv), WM_COMMAND, wintypes.WPARAM(CMD_TOGGLE_DESKTOP_ICONS), 0
        )
        return True

    def _show_window(self, visible: bool) -> bool:
        lv = find_list_view()
        if not lv:
            log.warning("未找到 SysListView32")
            return False
        user32.ShowWindow(wintypes.HWND(lv), SW_SHOW if visible else SW_HIDE)
        return True

    def _wait_for(self, visible: bool) -> bool:
        """轮询等待系统实际生效（explorer 处理命令有少量延迟）。"""
        deadline = time.monotonic() + _VERIFY_TIMEOUT
        while time.monotonic() < deadline:
            if self.is_visible() == visible:
                return True
            time.sleep(_VERIFY_INTERVAL)
        return self.is_visible() == visible
