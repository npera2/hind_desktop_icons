"""PyInstaller 打包入口。

用 ``python -m hind_desktop_icons`` 运行时会走这里。
"""

from __future__ import annotations

import multiprocessing
import sys


def _run() -> int:
    from .cli import main

    return main()


if __name__ == "__main__":
    # 本程序只用线程不用多进程，但 frozen 环境下加上这一行可以避免
    # 将来引入进程池时出现无限 fork。
    multiprocessing.freeze_support()
    sys.exit(_run())
