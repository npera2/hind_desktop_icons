"""hind_desktop_icons —— 一键隐藏 / 恢复 Windows 桌面图标。

对外只暴露 :func:`main`，作为 ``pyproject.toml` 里的脚本入口。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["main", "__version__"]


def main() -> None:
    """脚本入口：委托给 CLI。"""
    from .cli import main as _main

    raise SystemExit(_main())
