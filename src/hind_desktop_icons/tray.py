"""系统托盘界面。

交互约定（已核对 pystray 0.19.5 的 win32 后端实现 ``_win32.py`` 第 200 行）：
左键单击托盘图标 -> 触发 ``default=True`` 的菜单项 -> 切换图标显隐；
右键单击 -> 弹出菜单。

托盘图标用 Pillow 在内存里程序化绘制，不打包任何外部图片资源，
省掉 PyInstaller 的 ``sys._MEIPASS`` 路径处理，也让单文件体积更小。
"""

from __future__ import annotations

import ctypes
import logging
import threading

import pystray
from PIL import Image, ImageDraw

from . import autostart, logs
from .desktop import METHOD_COMMAND, DesktopIcons

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.MessageBoxW.restype = ctypes.c_int
user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]

MB_OK = 0x00000000
MB_ICONINFORMATION = 0x00000040
MB_ICONWARNING = 0x00000030
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000

APP_TITLE = "桌面图标开关"
ICON_NAME = "HindDesktopIcons"

_COLOR_VISIBLE = (37, 99, 235, 255)   # 图标可见：实心蓝
_COLOR_HIDDEN = (148, 155, 164, 255)  # 图标已隐藏：灰描边
_COLOR_SLASH = (220, 38, 38, 255)     # 已隐藏：红色斜杠

_BOX_SIZE = 20
_ORIGINS = (8, 36)
_BASE = 64  # 设计基准尺寸，其余尺寸按比例矢量缩放，避免缩小/放大后发虚


def make_icon(visible: bool, size: int = 64) -> Image.Image:
    """画一张 2x2 小方块构成的托盘图标，两种状态一眼可辨。

    坐标全部按 ``size / 64`` 等比换算后再绘制，所以任意尺寸都是清晰的，
    不会出现"画小图再放大导致模糊"的问题。
    """
    unit = size / _BASE
    box = _BOX_SIZE * unit
    radius = max(1, round(4 * unit))
    stroke = max(1, round(3 * unit))

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for top in _ORIGINS:
        for left in _ORIGINS:
            xy = (left * unit, top * unit, left * unit + box, top * unit + box)
            if visible:
                draw.rounded_rectangle(xy, radius=radius, fill=_COLOR_VISIBLE)
            else:
                draw.rounded_rectangle(xy, radius=radius, outline=_COLOR_HIDDEN, width=stroke)
    if not visible:
        draw.line(
            (10 * unit, 54 * unit, 54 * unit, 10 * unit),
            fill=_COLOR_SLASH,
            width=max(1, round(6 * unit)),
        )
    return image


def _message_box(text: str, title: str, *, warning: bool = False) -> None:
    """原生消息框。刻意不用 tkinter——打包时把它整个排除掉以减小体积。"""
    flags = MB_OK | MB_TOPMOST | MB_SETFOREGROUND | (
        MB_ICONWARNING if warning else MB_ICONINFORMATION
    )
    user32.MessageBoxW(None, text, title, flags)


class TrayApp:
    """托盘应用。所有对桌面图标状态的操作都串行化，避免托盘与 IPC 线程互相打架。"""

    def __init__(self, icons: DesktopIcons, version: str = "0.1.0") -> None:
        self._icons = icons
        self._version = version
        self._lock = threading.Lock()
        self._touched = False  # 本次运行是否改过图标状态，用于退出时决定要不要还原
        self.icon = pystray.Icon(
            ICON_NAME,
            make_icon(icons.is_visible()),
            APP_TITLE,
            self._build_menu(),
        )

    # ---------- 菜单 ----------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self._toggle_label, self._on_toggle, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "开机自动启动",
                self._on_autostart,
                checked=lambda _item: autostart.is_enabled(),
            ),
            pystray.MenuItem("打开日志", self._on_open_log),
            pystray.MenuItem("关于", self._on_about),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_exit),
        )

    def _toggle_label(self, _item=None) -> str:
        """菜单文字随当前状态变化；pystray 会把菜单项作为参数传进来。"""
        return "隐藏桌面图标" if self._icons.is_visible() else "显示桌面图标"

    # ---------- 状态 ----------

    def _refresh(self) -> None:
        visible = self._icons.is_visible()
        self.icon.icon = make_icon(visible)
        self.icon.title = f"{APP_TITLE}（图标已{'显示' if visible else '隐藏'}）"
    def toggle(self, *, source: str = "user") -> bool:
        """切换一次并刷新托盘表现。"""
        with self._lock:
            before = self._icons.is_visible()
            ok = self._icons.toggle()
            self._refresh()
            after = self._icons.is_visible()
            if before != after:
                self._touched = True
            log.info(
                "%s 触发切换：%s -> %s（成功=%s，方式=%s）",
                source,
                "显示" if before else "隐藏",
                "显示" if after else "隐藏",
                ok,
                self._icons.method,
            )
            if not ok:
                _message_box(
                    "未能切换桌面图标。\n\n"
                    "通常是桌面图标控件被第三方桌面工具接管，或资源管理器未在运行。\n"
                    "详细信息见日志（托盘菜单 -> 打开日志）。",
                    APP_TITLE,
                    warning=True,
                )
            return ok

    def refresh_only(self) -> None:
        """只在外部（用户手动改了状态）之后同步托盘表现。"""
        with self._lock:
            self._refresh()

    # ---------- 菜单动作 ----------

    def _on_toggle(self, _icon=None, _item=None) -> None:
        self.toggle(source="托盘")

    def _on_autostart(self, _icon=None, _item=None) -> None:
        target = not autostart.is_enabled()
        if not autostart.set_enabled(target):
            _message_box("修改开机自启失败，请查看日志。", APP_TITLE, warning=True)
            return
        log.info("开机自启已%s", "开启" if target else "关闭")
        self.icon.update_menu()

    def _on_open_log(self, _icon=None, _item=None) -> None:
        try:
            logs.open_log_file()
        except OSError as exc:
            log.error("打开日志失败：%s", exc)

    def _on_about(self, _icon=None, _item=None) -> None:
        info = self._icons.info()
        method = (
            "WM_COMMAND 0x7402（系统菜单同款）"
            if info["method"] == METHOD_COMMAND
            else "ShowWindow 降级方案"
        )
        text = (
            f"{APP_TITLE}  v{self._version}\n\n"
            f"当前状态：图标已{'显示' if info['visible'] else '隐藏'}\n"
            f"切换方式：{method}\n"
            f"桌面容器：{info['defview_parent_class'] or '未知'}"
            f"（DefView 0x{info['defview']:X}）\n"
            f"系统开关：HideIcons = {info['hideicons']}\n\n"
            f"左键单击托盘图标 = 隐藏 / 恢复\n"
            f"右键单击 = 打开本菜单\n\n"
            f"日志：{logs.log_path()}"
        )
        _message_box(text, APP_TITLE)

    def _on_exit(self, _icon=None, _item=None) -> None:
        log.info("用户从托盘退出")
        self.shutdown()

    # ---------- 运行 ----------

    def _restore_if_needed(self) -> None:
        """退出时若"我们把它藏起来了、且现在还藏着"，就恢复显示。

        只在本次运行确实改动过状态时才还原，避免动到用户自己手动设置的隐藏状态。
        """
        with self._lock:
            if self._touched and not self._icons.is_visible():
                log.info("退出时图标仍处于隐藏状态，自动恢复显示")
                self._icons.set_visible(True)

    def shutdown(self) -> None:
        """退出流程：还原必要的状态 -> 停止托盘。"""
        try:
            self._restore_if_needed()
        finally:
            self.stop()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception as exc:  # pragma: no cover - pystray 内部状态异常
            log.warning("停止托盘失败：%s", exc)

    def run(self) -> None:
        log.info("托盘已启动，当前图标：%s", "显示" if self._icons.is_visible() else "隐藏")
        self.icon.run()
        log.info("托盘已退出")
