"""日志与运行环境处理。

打包成 ``--windowed`` 的 exe 后，``sys.stdout`` / ``sys.stderr`` 是 ``None``，
任何 ``print()`` 都会抛 ``AttributeError`` 并且**在无控制台的情况下静默崩掉**。
因此入口第一件事就是兜住这两个流，全部输出改走日志文件。

日志位置固定在 ``%LOCALAPPDATA%\\HindDesktopIcons\\app.log``：
绝不能写 exe 同目录——exe 常被放在桌面、Program Files 或同步盘里，可能无写权限，
还会把日志混进用户看得见的地方。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_DIR_NAME = "HindDesktopIcons"
LOG_FILE_NAME = "app.log"

_configured: Path | None = None


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / APP_DIR_NAME


def log_path() -> Path:
    return log_dir() / LOG_FILE_NAME


def guard_std_streams() -> None:
    """保证 stdout / stderr 永远可用，且输出中文时不会崩。

    两种必须兜住的情况：

    1. ``frozen`` + ``--windowed`` 下 ``sys.stdout`` / ``sys.stderr`` 是 ``None``，
       任何 ``print()`` 都会抛 ``AttributeError``，而且因为看不到控制台，会静默崩掉。
    2. 输出被重定向、或系统默认代码页不支持中文时（例如英文版 Windows 的 cp1252），
       打印中文会抛 ``UnicodeEncodeError``。这里把 errors 放宽成 ``replace``，
       编不出来的字符退化成 ``?``，但程序不会崩。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            try:
                stream = open(os.devnull, "w", encoding="utf-8")
                setattr(sys, name, stream)
            except OSError:  # pragma: no cover - 极端环境
                continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - 非标准流
                pass


def setup(debug: bool = False) -> Path:
    """初始化日志，返回日志文件路径。重复调用只生效一次。"""
    global _configured
    guard_std_streams()
    if _configured is not None:
        return _configured

    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8", delay=False
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.addHandler(handler)

    # 源码方式运行时（有真实控制台）才同步输出到屏幕；打包后 stderr 是 devnull。
    if not getattr(sys, "frozen", False) and sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        root.addHandler(console)

    logging.getLogger(__name__).info(
        "日志初始化完成：%s（frozen=%s，python=%s）",
        path,
        getattr(sys, "frozen", False),
        sys.version.split()[0],
    )
    _configured = path
    return path


def open_log_file() -> None:
    """用系统默认程序打开日志文件。"""
    setup()
    path = log_path()
    if not path.exists():
        path.write_text("", encoding="utf-8")
    os.startfile(str(path))  # noqa: S606 - 打开自己生成的日志，路径可控
