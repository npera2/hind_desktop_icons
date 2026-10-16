"""P0 机制验证脚本（临时工具，不进最终交付）。

目的：在真实机器上确认
  1. 桌面图标控件 SysListView32 的查找链（Progman 与 WorkerW 两种宿主）；
  2. 向 SHELLDLL_DefView 发送 WM_COMMAND = 0x7402 是否真能切换「显示桌面图标」；
  3. 注册表 HideIcons 与 IsWindowVisible 两种状态读法是否可靠；
  4. ShowWindow 降级方案是否可用。

脚本会临时切换桌面图标显隐，结束时自动还原到运行前的状态。
"""

import ctypes
import sys
import time
import winreg
from ctypes import wintypes

if sys.stdout is None:
    sys.stdout = open("nul", "w", encoding="utf-8")
else:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

WM_COMMAND = 0x0111
CMD_TOGGLE_DESKTOP_ICONS = 0x7402
SW_HIDE, SW_SHOW = 0, 5

ADV_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"


def cls_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def child(hwnd: int, cls: str, after: int = 0) -> int:
    return user32.FindWindowExW(
        wintypes.HWND(hwnd), wintypes.HWND(after), cls, None
    ) or 0


def all_defviews() -> list[tuple[int, int]]:
    """返回 [(defview_hwnd, parent_hwnd), ...]，父窗口可能是 Progman 或 WorkerW。"""
    out: list[tuple[int, int]] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):
        h = hwnd if isinstance(hwnd, int) else ctypes.cast(hwnd, ctypes.c_void_p).value
        dv = child(h, "SHELLDLL_DefView")
        if dv:
            out.append((dv, h))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def registry_hidden() -> bool | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ADV_KEY) as k:
            v, _ = winreg.QueryValueEx(k, "HideIcons")
            return bool(v)
    except FileNotFoundError:
        return None


def snapshot(tag: str) -> dict:
    views = all_defviews()
    lvs = [(dv, child(dv, "SysListView32")) for dv, _ in views]
    s = {
        "hideicons": registry_hidden(),
        "defviews": views,
        "listviews": lvs,
        "lv_visible": [bool(user32.IsWindowVisible(wintypes.HWND(lv))) for _, lv in lvs],
        "dv_visible": [bool(user32.IsWindowVisible(wintypes.HWND(dv))) for dv, _ in views],
    }
    print(f"[{tag}] HideIcons={s['hideicons']}  DefView数={len(views)}")
    for (dv, parent), (_, lv), lvv, dvv in zip(views, lvs, s["lv_visible"], s["dv_visible"]):
        print(
            f"    父窗口={cls_name(parent)}(0x{parent:X})  DefView=0x{dv:X} "
            f"可见={dvv}  ListView=0x{lv:X} 可见={lvv}"
        )
    return s


def send_toggle(hwnd: int, target_name: str) -> bool:
    before = registry_hidden()
    before_vis = [bool(user32.IsWindowVisible(wintypes.HWND(lv))) for _, lv in
                  [(dv, child(dv, "SysListView32")) for dv, _ in all_defviews()]]
    user32.SendMessageW(
        wintypes.HWND(hwnd), WM_COMMAND, wintypes.WPARAM(CMD_TOGGLE_DESKTOP_ICONS), 0
    )
    time.sleep(0.45)
    after = registry_hidden()
    after_vis = [bool(user32.IsWindowVisible(wintypes.HWND(lv))) for _, lv in
                 [(dv, child(dv, "SysListView32")) for dv, _ in all_defviews()]]
    changed = (after != before) or (after_vis != before_vis)
    print(f"  发往 {target_name}: HideIcons {before} -> {after} | ListView可见 {before_vis} -> {after_vis}"
          f"  => {'生效' if changed else '无效'}")
    return changed


def main() -> int:
    print("=" * 72)
    print("P0 机制验证开始")
    print("=" * 72)
    base = snapshot("初始")

    views = all_defviews()
    if not views:
        print("!! 未找到 SHELLDLL_DefView，桌面图标机制与预期不符，需重新调研")
        return 2

    original_hidden = base["hideicons"] or False

    print("\n--- 测试 1：WM_COMMAND 0x7402 发往 SHELLDLL_DefView ---")
    changed = False
    for dv, parent in views:
        if send_toggle(dv, f"SHELLDLL_DefView(0x{dv:X}, 父={cls_name(parent)})"):
            changed = True
            break

    if not changed:
        print("\n--- 测试 1b：改发父窗口（Progman / WorkerW）---")
        for dv, parent in views:
            send_toggle(parent, f"{cls_name(parent)}(0x{parent:X})")
            changed = True
            break

    snapshot("切换后")

    print("\n--- 测试 2：状态读法一致性 ---")
    lv = base["listviews"][0][1] if base["listviews"] and base["listviews"][0][1] else 0
    print(f"  ListView 句柄取到: {'是' if lv else '否'}")
    print(f"  当前 HideIcons = {registry_hidden()}")

    print("\n--- 测试 3：ShowWindow 降级方案 ---")
    if lv:
        before = bool(user32.IsWindowVisible(wintypes.HWND(lv)))
        user32.ShowWindow(wintypes.HWND(lv), SW_HIDE)
        time.sleep(0.3)
        print(f"  SW_HIDE: ListView 可见 {before} -> {bool(user32.IsWindowVisible(wintypes.HWND(lv)))}")
        user32.ShowWindow(wintypes.HWND(lv), SW_SHOW)
        time.sleep(0.3)
        print(f"  SW_SHOW: ListView 可见 -> {bool(user32.IsWindowVisible(wintypes.HWND(lv)))}")
    else:
        print("  未取到 ListView，跳过")

    print("\n--- 还原 ---")
    now_hidden = registry_hidden() or False
    if now_hidden != original_hidden:
        for dv, parent in views:
            send_toggle(dv, "还原")
            break
        snapshot("还原后")
    else:
        print("  无需还原")

    print("\n" + "=" * 72)
    print(f"结论：WM_COMMAND 0x7402 在主路径上{'可用' if changed else '不可用'}")
    print("=" * 72)
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
