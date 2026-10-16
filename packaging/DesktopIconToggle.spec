# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台、零外部资源。

构建命令（在项目根目录执行）：
    # 单文件（默认）
    uv run --no-sync pyinstaller --noconfirm --clean packaging/DesktopIconToggle.spec

    # 文件夹版（启动快得多，用于对比）
    HDI_ONEDIR=1 uv run --no-sync pyinstaller --noconfirm --clean packaging/DesktopIconToggle.spec

两种模式的取舍（本项目实测数据，11 MB 单文件 vs 36 MB 文件夹）：
* onefile  启动约 7 秒/次 —— 每次运行都要把 ~36 MB 内容解压到 %TEMP% 并逐个被杀软扫描。
* onedir   启动约 0.7 秒/次 —— 不涉及解压。
所以：只双击一次常驻，用 onefile；把 exe 当作反复点击的开关，用 onedir。

几个刻意的选择：
* ``console=False``  —— 不弹黑窗，程序只通过托盘与消息框与用户交互。
* ``upx=False``      —— UPX 压缩会明显抬高杀软误报率，体积换安全不划算。
* ``hiddenimports``  —— pystray 的后端是**函数内动态导入**（``from . import _win32``），
  PyInstaller 静态分析扫不到，不显式声明的话打出来的 exe 一启动就 ImportError。
* ``excludes``       —— 把 GUI 无关的大件排除掉，压缩体积。
* ``_DROP_BINARIES`` —— Pillow 的 hook 会把 PIL 目录下所有 .pyd 全量收进来，
  其中 ``_avif`` 单独就占 4.3 MB。excludes 管不到 hook 收集的二进制，
  所以在 spec 里直接过滤 ``a.binaries``。本程序只用 ImageDraw 画几何图形，
  不需要 AVIF/WebP 解码、色彩管理（ImageCms）和字体渲染（_imagingft）。
"""

import os

_SPEC_DIR = SPECPATH  # noqa: F821 - PyInstaller 注入：本 spec 文件所在目录
ROOT = os.path.dirname(_SPEC_DIR)

SRC = os.path.join(ROOT, "src")
ENTRY = os.path.join(_SPEC_DIR, "exe_entry.py")
ICON = os.path.join(ROOT, "assets", "app.ico")
VERSION_FILE = os.path.join(_SPEC_DIR, "version_info.txt")

# 需要从最终二进制里剔除的插件（按文件名子串匹配）。
# excludes 管不到 hook 收集来的 .pyd/.dll，这里直接过滤 a.binaries。
# libcrypto/libssl 合计 9.1 MB，本程序完全不用 TLS，是 PyInstaller 依赖图的连带产物。
_DROP_BINARIES = (
    "_avif",
    "_webp",
    "_imagingcms",
    "_imagingft",
    "_ssl",
    "_hashlib",
    "libcrypto",
    "libssl",
)

a = Analysis(
    [ENTRY],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pystray._win32",   # 动态导入，必须显式声明
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "doctest",
        "pydoc",
        "pdb",
        "test",
        "distutils",
        "setuptools",
        "pip",
        # 顺手剔掉的无关 Pillow 插件模块
        "PIL.AvifImagePlugin",
        "PIL.WebPImagePlugin",
        "PIL.ImageCms",
        # 本程序不联网。剔掉 TLS 依赖能把 libcrypto-3-x64.dll(7.6MB) +
        # libssl-3-x64.dll(1.5MB) 一起甩掉——它们是 PyInstaller 依赖图的连带产物。
        # urllib.request 是 `try: import ssl / except ImportError` 的写法，不受影响。
        "ssl",
        "_ssl",
        "_hashlib",
        # 下面这些确认过不在 PyInstaller 运行时钩子的启动路径上。
        # 教训：绝不能剔 urllib / email / http —— pyi_rth_inspect -> zipfile ->
        # pathlib -> urllib.parse 是冻结点启动的必经路径，剔了会直接起不来。
        "xml",
        "pyexpat",
        "_elementtree",
        "decimal",
        "_decimal",
        "bz2",
        "_bz2",
        "lzma",
        "_lzma",
    ],
    noarchive=False,
    optimize=1,
)

# excludes 管不到 hook 收集来的 .pyd，这里直接过滤
a.binaries = [item for item in a.binaries if not any(tag in item[0] for tag in _DROP_BINARIES)]

pyz = PYZ(a.pure)

_ONE_DIR = os.environ.get("HDI_ONEDIR") == "1"

if _ONE_DIR:
    # 文件夹版：不打包成一个文件，启动时无需解压
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="DesktopIconToggle",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
        version=VERSION_FILE,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="DesktopIconToggle-portable",
    )
else:
    # 单文件：双击即用，代价是每次启动都要自解压
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="DesktopIconToggle",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
        version=VERSION_FILE,
    )
