"""生成 exe 图标与托盘图标预览（构建用工具，不属于运行时）。

产物：
    assets/app.ico                        多尺寸 exe 图标
    build/icon_preview_visible.png        托盘图标预览（显示态，放大 4 倍便于肉眼检查）
    build/icon_preview_hidden.png         托盘图标预览（隐藏态）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hind_desktop_icons.tray import make_icon  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "build").mkdir(exist_ok=True)

    # exe 图标：256 原生绘制，交给 Pillow 生成多尺寸 ICO
    make_icon(True, 256).save(ROOT / "assets" / "app.ico", format="ICO", sizes=ICO_SIZES)
    print(f"已生成 {ROOT / 'assets' / 'app.ico'}")

    # 托盘图标预览：模拟任务栏里的两种状态（含放大版，方便肉眼判断小尺寸可读性）
    for state, name in ((True, "visible"), (False, "hidden")):
        canvas = make_icon(state, 128)
        canvas.save(ROOT / "build" / f"icon_preview_{name}.png")
        print(f"已生成 build/icon_preview_{name}.png")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
