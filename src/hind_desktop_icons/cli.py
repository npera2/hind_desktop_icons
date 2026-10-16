"""命令行入口与启动分发。

启动语义（混合模式）
--------------------
* 无参数双击：若没有常驻实例 -> 启动托盘并**立即隐藏**图标；
              若已有常驻实例 -> 通知它切换一次，本进程毫秒级退出。
  于是"双击一次隐藏、再双击一次恢复"成立，且不必反复启停进程。
* ``--toggle``：只切换一次就退出，不要托盘（纯瞬时用法）。
* ``--no-start-toggle``：只启动托盘，不动图标（开机自启使用）。
* ``--quit``：让已常驻的实例退出。
* ``--status`` / ``--autostart on|off|status``：查询与设置。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from . import autostart, ipc, logs
from .desktop import METHOD_COMMAND, DesktopIcons
from .tray import TrayApp

log = logging.getLogger(__name__)

VERSION = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="DesktopIconToggle",
        description="一键隐藏 / 恢复 Windows 桌面图标",
    )
    parser.add_argument("--toggle", action="store_true", help="只切换一次后退出，不常驻托盘")
    parser.add_argument(
        "--no-start-toggle", action="store_true", help="只启动托盘，不改动图标（开机自启使用）"
    )
    parser.add_argument("--quit", action="store_true", help="让已常驻的实例退出")
    parser.add_argument("--status", action="store_true", help="打印当前状态后退出")
    parser.add_argument(
        "--autostart", choices=("on", "off", "status"), help="开机自动启动开关"
    )
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _emit(text: str) -> None:
    """同时写控制台与日志。打包后没有控制台，print 是空操作，但日志仍留痕。"""
    print(text)
    log.info(text.replace("\n", " | "))


def _fatal(message: str) -> None:
    """致命错误：弹原生消息框告知用户，避免静默失败。"""
    try:
        from .tray import _message_box

        _message_box(f"程序遇到问题：\n\n{message}\n\n详情见日志：{logs.log_path()}", "桌面图标开关", warning=True)
    except Exception:  # pragma: no cover - 消息框都弹不出来就只能靠日志了
        log.exception("弹出错误提示失败")


# ---------- 各子命令 ----------


def _cmd_status() -> int:
    icons = DesktopIcons()
    info = icons.info()
    method = "WM_COMMAND 0x7402" if info["method"] == METHOD_COMMAND else "ShowWindow（降级）"
    _emit(
        "\n".join(
            [
                f"当前状态   : 图标已{'显示' if info['visible'] else '隐藏'}",
                f"切换方式   : {method}",
                f"系统开关   : HideIcons = {info['hideicons']}",
                f"桌面容器   : {info['defview_parent_class'] or '未知'} (0x{info['defview_parent']:X})",
                f"DefView    : 0x{info['defview']:X}",
                f"图标控件   : 0x{info['listview']:X}",
                f"日志文件   : {logs.log_path()}",
            ]
        )
    )
    return 0


def _cmd_autostart(action: str) -> int:
    if action == "status":
        _emit(f"开机自启：{'已开启' if autostart.is_enabled() else '未开启'}")
        return 0
    want = action == "on"
    ok = autostart.set_enabled(want)
    _emit(f"开机自启{action}：{'成功' if ok else '失败'}")
    return 0 if ok else 1


def _cmd_toggle() -> int:
    icons = DesktopIcons()
    before = icons.is_visible()
    ok = icons.toggle()
    _emit(
        f"切换{'成功' if ok else '失败'}："
        f"{'显示' if before else '隐藏'} -> {'显示' if icons.is_visible() else '隐藏'}"
        f"（方式：{icons.method}）"
    )
    return 0 if ok else 1


def _cmd_quit() -> int:
    if ipc.signal_quit():
        _emit("已通知常驻实例退出")
    else:
        _emit("没有正在运行的常驻实例")
    return 0


# ---------- 常驻模式 ----------


def _run_tray(*, start_toggle: bool) -> int:
    singleton = ipc.Singleton()
    acquired, already_running = singleton.acquire()
    if not acquired:
        return 1

    if already_running:
        # 已有实例：本次只负责把"切换"的意图转达过去
        if not start_toggle:
            log.info("已有实例在运行，本次不执行任何动作")
            singleton.release()
            return 0
        if ipc.signal_toggle():
            log.info("已通知常驻实例切换桌面图标")
            singleton.release()
            return 0
        log.warning("检测到常驻实例，但通知失败（对方可能正在启动或已异常退出）")
        singleton.release()
        return 1

    channel = ipc.SignalChannel()
    try:
        channel.create()
    except OSError as exc:
        log.error("创建进程间事件失败：%s", exc)
        channel.close()
        singleton.release()
        return 1

    icons = DesktopIcons()
    app = TrayApp(icons, VERSION)
    stopping = threading.Event()

    def _ipc_worker() -> None:
        while not stopping.is_set():
            signal = channel.wait(300)
            if signal == "toggle":
                app.toggle(source="再次双击 exe")
            elif signal == "quit":
                log.info("收到退出信号")
                app.shutdown()
                return

    worker = threading.Thread(target=_ipc_worker, name="ipc", daemon=True)
    worker.start()

    if start_toggle:
        app.toggle(source="首次启动")

    try:
        app.run()
    finally:
        stopping.set()
        channel.close()
        singleton.release()
        log.info("常驻实例已完全退出")
    return 0


# ---------- 入口 ----------


def _dispatch(args: argparse.Namespace) -> int:
    if args.status:
        return _cmd_status()
    if args.autostart:
        return _cmd_autostart(args.autostart)
    if args.quit:
        return _cmd_quit()
    if args.toggle:
        return _cmd_toggle()
    return _run_tray(start_toggle=not args.no_start_toggle)


def main(argv: list[str] | None = None) -> int:
    # 必须先兜住 stdout/stderr：--windowed 打包后它们是 None，
    # 而 argparse 的 --version 会直接往 stdout 写。
    logs.guard_std_streams()
    args = build_parser().parse_args(argv)
    logs.setup(debug=args.debug)
    log.debug("启动参数：%s", vars(args))
    try:
        return _dispatch(args)
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，任何异常都要有记录
        log.exception("未处理的异常")
        _fatal(repr(exc))
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
