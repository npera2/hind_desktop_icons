"""exe 打包入口。

刻意不在 ``src/hind_desktop_icons/__main__.py`` 上直接打包：
PyInstaller 会把入口脚本当作顶层模块 ``__main__`` 分析，
那时包内的相对导入（``from .cli import main``）解析不到父包，报
"attempted relative import with no known parent package"。
用一个绝对导入的顶层启动脚本最稳。
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

# 源码方式运行时（未安装到 site-packages）也能直接跑
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from hind_desktop_icons.cli import main  # noqa: E402


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
