"""构建原生启动器（最终的单文件 exe）。

流程
----
1. 用 PyInstaller 打出 onedir 载荷（文件夹版，不压缩、启动快）；
2. 把载荷整包塞进自定义容器（不压缩，纯拼接，C 侧零依赖）；
3. 用载荷内容的 SHA-256 前 8 位生成"构建号"，写进路径
   %LOCALAPPDATA%\\HindDesktopIcons\\bin\\<构建号>\\ —— 载荷一变，构建号就变，
   于是旧版本不会被误用，新版本会重新解压一次；
4. 生成 .rc（图标 + 版本信息 + 载荷 RCDATA），windres 编译资源，gcc 链接出单个 exe。

为什么载荷不压缩：这台机器的 MinGW 没带 zlib，而 Windows 自带的解压 API 格式又不好在
Python 侧生产。不压缩换来 C 侧零依赖、代码简单、解压更快（纯文件写入），代价是 exe 大一些。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "DesktopIconToggle.spec"
LAUNCHER_C = ROOT / "packaging" / "launcher.c"
ICON = ROOT / "assets" / "app.ico"
# 载荷建到 build/ 下而不是 dist/：dist/ 只留最终交付的单个 exe；
# 且 build/ 里的旧目录可以用改名的方式挪开，避开删目录的限制。
PAYLOAD_DIST = ROOT / "build" / "payload_dist"
PAYLOAD_DIR = PAYLOAD_DIST / "DesktopIconToggle-portable"
TRASH_DIR = ROOT / "build" / "trash"
WORK = ROOT / "build" / "launcher"

# gcc / windres 的搜索顺序：PATH → 这些常见目录 → 报错。
# 最后一项是 GitHub Actions 的 windows runner 上 MSYS2 的位置。
TOOL_DIRS = (
    Path(r"C:\Program Files\mingw64\bin"),
    Path(r"C:\Program Files (x86)\mingw64\bin"),
    Path(r"C:\mingw64\bin"),
    Path(r"C:\msys64\mingw64\bin"),
)

MAGIC = b"HDIP"
FORMAT_VERSION = 1
RES_ICON = 101
RES_PAYLOAD = 1001
APP_VERSION = "0.1.0"


def find_tool(exe_name: str, explicit: str | None, flag: str) -> Path:
    """定位 gcc / windres：显式指定 > PATH > 常见安装目录。

    PATH 上可能混着 MSYS 目标的编译器（`C:\\msys64\\usr\\bin`），那种编出来的 exe
    依赖 msys-2.0.dll，在普通 Windows 上跑不起来，所以这里会跳过它们继续找。
    """
    if explicit:
        path = Path(explicit)
        if path.exists():
            if _is_msys_target(path):
                raise SystemExit(
                    f"{flag} 指向了 MSYS 环境目录：{path}\n"
                    "用它构建的 exe 会依赖 msys-2.0.dll，在普通 Windows 上无法运行。\n"
                    f"请改用 MinGW-w64 版本，例如 C:/msys64/mingw64/bin/{exe_name}"
                )
            return path
        print(f"提示：{flag} 指定的 {path} 不存在，改为自动查找")

    candidates: list[Path] = []
    found = shutil.which(exe_name)
    if found:
        candidates.append(Path(found))
    candidates.extend(directory / exe_name for directory in TOOL_DIRS)

    for candidate in candidates:
        if candidate.exists() and not _is_msys_target(candidate):
            return candidate

    if any(candidate.exists() for candidate in candidates):
        raise SystemExit(
            f"找到的 {exe_name} 都是 MSYS 目标的，不能用。\n"
            f"请安装 MinGW-w64 工具链，或用 {flag} 指向它，例如 "
            f"C:/msys64/mingw64/bin/{exe_name}"
        )

    raise SystemExit(
        f"找不到 {exe_name}。请安装 MinGW-w64，或用 {flag} 指定它的完整路径。"
    )


def _is_msys_target(path: Path) -> bool:
    """判断是否为 MSYS 目标的编译器（C:\\msys64\\usr\\bin 下的那些）。"""
    lowered = str(path).lower().replace("/", "\\")
    return "\\msys64\\usr\\bin" in lowered or "\\msys\\usr\\bin" in lowered


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    printable = " ".join(str(c) for c in cmd)
    print(f"$ {printable}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env)
    if result.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {result.returncode}）：{printable}")


def build_payload() -> None:
    """调 PyInstaller 生成 onedir 载荷。"""
    # PyInstaller 会自己删掉已存在的目标目录，而删目录在受限环境里可能被拦。
    # 先把它改名挪进 build/trash/，构建目标就是干净的，无需任何删除操作。
    if PAYLOAD_DIR.exists():
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = f"{PAYLOAD_DIR.name}-{int(time.time())}"
        PAYLOAD_DIR.rename(TRASH_DIR / stamp)
        print(f"旧载荷已挪至 build/trash/{stamp}")

    env = dict(os.environ)
    env["HDI_ONEDIR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--workpath",
            str(ROOT / "build" / "pyi_dir"),
            "--distpath",
            str(PAYLOAD_DIST),
            str(SPEC),
        ],
        cwd=ROOT,
        env=env,
    )


def collect_files() -> list[tuple[str, Path]]:
    """收集载荷里所有文件，返回 [(容器内相对路径, 本地路径)]。"""
    if not PAYLOAD_DIR.is_dir():
        raise SystemExit(f"找不到载荷目录：{PAYLOAD_DIR}\n请先运行 PyInstaller（去掉 --no-pyinstaller）。")

    files: list[tuple[str, Path]] = []
    for path in sorted(PAYLOAD_DIR.rglob("*")):
        if path.is_file():
            rel = path.relative_to(PAYLOAD_DIR).as_posix()
            files.append((rel, path))

    if not files:
        raise SystemExit(f"载荷目录是空的：{PAYLOAD_DIR}")
    return files


def build_container(files: list[tuple[str, Path]]) -> bytes:
    """按 launcher.c 里定义的格式拼出容器。"""
    table = bytearray()
    blobs = bytearray()
    header_size = 12
    table_size = sum(12 + len(name.encode("utf-8")) for name, _ in files)
    offset = header_size + table_size

    for name, path in files:
        name_bytes = name.encode("utf-8")
        data = path.read_bytes()
        table += struct.pack("<HHII", len(name_bytes), 0, len(data), offset)
        table += name_bytes
        blobs += data
        offset += len(data)

    header = MAGIC + struct.pack("<II", FORMAT_VERSION, len(files))
    return bytes(header + table + blobs)


def write_rc(rc_path: Path, payload_path: Path, build_id: str, version: str) -> None:
    def q(p: Path) -> str:
        return str(p).replace("\\", "\\\\")

    numeric_version = f"{version}.0"
    rc = f"""#include <windows.h>

{RES_ICON} ICON "{q(ICON)}"
{RES_PAYLOAD} RCDATA "{q(payload_path)}"

1 VERSIONINFO
FILEVERSION {version.replace('.', ',')},0
PRODUCTVERSION {version.replace('.', ',')},0
FILEOS 0x40004
FILETYPE 0x1
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "080404B0"
    BEGIN
      VALUE "CompanyName", "npera2"
      VALUE "FileDescription", "桌面图标开关 - 一键隐藏/恢复桌面图标"
      VALUE "FileVersion", "{numeric_version}"
      VALUE "InternalName", "DesktopIconToggle"
      VALUE "OriginalFilename", "DesktopIconToggle.exe"
      VALUE "ProductName", "桌面图标开关"
      VALUE "ProductVersion", "{numeric_version}"
      VALUE "Comments", "launcher build {build_id}"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x804, 1200
  END
END
"""
    rc_path.write_text(rc, encoding="utf-8")


def compile_launcher(rc_path: Path, build_id: str, gcc: Path, windres: Path) -> Path:
    res_path = rc_path.with_suffix(".res")
    out_exe = ROOT / "dist" / "DesktopIconToggle.exe"

    # dist/ 是 gitignore 的构建输出目录，全新检出的仓库里并不存在——
    # 必须自己创建，否则 gcc 链接时会报
    # "cannot open output file ...\dist\DesktopIconToggle.exe: No such file or directory"。
    try:
        out_exe.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(f"无法创建输出目录 {out_exe.parent}：{exc}")

    # windres 默认的预处理器是它自己目录下的 gcc，而 "C:\Program Files\..." 里的空格
    # 会让它拼出来的命令行断掉（'C:\Program' 不是内部或外部命令）。
    # 解法：把 mingw 的 bin 加进 PATH，用裸名 gcc 调起，并补上预处理参数
    # （只给 --preprocessor 会丢掉 -E，gcc 会把 .rc 当源码去链接）。
    env = dict(os.environ)
    env["PATH"] = str(gcc.parent) + os.pathsep + env.get("PATH", "")

    run(
        [
            str(windres),
            "--codepage=65001",
            "--preprocessor=gcc",
            "--preprocessor-arg=-E",
            "--preprocessor-arg=-xc-header",
            "--preprocessor-arg=-DRC_INVOKED",
            "-O",
            "coff",
            "-o",
            str(res_path),
            str(rc_path),
        ],
        cwd=WORK,
        env=env,
    )
    run(
        [
            str(gcc),
            "-O2",
            "-s",
            "-mwindows",
            "-Wall",
            "-DUNICODE",
            "-D_UNICODE",
            f'-DHDI_BUILD_ID="{build_id}"',
            "-o",
            str(out_exe),
            str(LAUNCHER_C),
            str(res_path),
        ],
        cwd=ROOT,
        env=env,
    )
    return out_exe


def main() -> int:
    parser = argparse.ArgumentParser(description="构建原生启动器单文件 exe")
    parser.add_argument("--no-pyinstaller", action="store_true", help="复用已有的 onedir 载荷，跳过 PyInstaller")
    parser.add_argument("--gcc", default=None, help="gcc 路径（默认自动在 PATH 与常见目录中查找）")
    parser.add_argument("--windres", default=None, help="windres 路径（同上）")
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help="写入 exe 版本信息的版本号，形如 0.1.0（默认 %(default)s）",
    )
    args = parser.parse_args()

    version = args.version.strip()
    if not version or any(c not in "0123456789." for c in version):
        raise SystemExit(f"版本号只能是数字与点，收到：{args.version!r}")

    gcc = find_tool("gcc.exe", args.gcc, "--gcc")
    windres = find_tool("windres.exe", args.windres, "--windres")
    print(f"gcc     : {gcc}")
    print(f"windres : {windres}")

    WORK.mkdir(parents=True, exist_ok=True)
    ICON.parent.mkdir(exist_ok=True)
    if not ICON.exists():
        raise SystemExit(f"找不到图标 {ICON}，请先运行 tools/make_assets.py")

    if not args.no_pyinstaller:
        build_payload()

    files = collect_files()
    container = build_container(files)
    digest = hashlib.sha256(container).hexdigest()[:8]
    build_id = f"{version}-{digest}"

    payload_path = WORK / "payload.bin"
    payload_path.write_bytes(container)
    rc_path = WORK / "launcher.rc"
    write_rc(rc_path, payload_path, build_id, version)

    raw_mb = sum(p.stat().st_size for _, p in files) / 1024 / 1024
    print(f"\n载荷：{len(files)} 个文件，{raw_mb:.1f} MB，容器 {len(container) / 1024 / 1024:.1f} MB")
    print(f"构建号：{build_id}")

    out_exe = compile_launcher(rc_path, build_id, gcc, windres)
    size_mb = out_exe.stat().st_size / 1024 / 1024
    print(f"\n完成：{out_exe}（{size_mb:.1f} MB）")
    print(f"安装位置：%LOCALAPPDATA%\\HindDesktopIcons\\bin\\{build_id}\\")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
