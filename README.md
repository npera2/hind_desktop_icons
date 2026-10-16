# 桌面图标开关（DesktopIconToggle）

> 一个 Windows 小工具：**双击一次隐藏桌面上的所有图标，再双击一次恢复显示。**
> 单个 exe，免安装、免管理员权限、不弹控制台窗口。

[![Release](https://img.shields.io/github/v/release/npera2/hind_desktop_icons?display_name=tag&sort=semver)](https://github.com/npera2/hind_desktop_icons/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6)

---

## 简介

截图、录屏、做演示、想安安静静看壁纸的时候，桌面上的图标总是碍事。Windows 自带的办法是
「右键桌面 → 查看 → 取消勾选"显示桌面图标"」，三步，而且每次都要来一遍。

这个小工具把这件事压成**点一下**：双击 exe 隐藏，再双击就恢复。

实现上有两个不太常见的地方：

- **单文件 exe 里塞了一个 C 写的原生启动器。**
  PyInstaller 的 `--onefile` 每次运行都要把自己解压一遍，本机实测要 **7 秒**；
  而这个启动器在"已有常驻实例"时只做一次命名事件通知（`OpenEventW + SetEvent`），
  **连 Python 都不启动**，所以重复点击是秒回的。详见「[性能](#性能)」。
- **隐藏图标不靠驱动、不靠换壁纸**，而是向 `explorer.exe` 的桌面视图窗口发一条系统命令
  （`WM_COMMAND = 0x7402`，等价于桌面右键菜单里的「显示桌面图标」），
  因此与系统菜单的勾选状态完全同步，资源管理器重启后状态也延续。

---

## 主要功能

- 一键隐藏 / 恢复所有桌面图标，键盘鼠标都不用离开当前位置
- 托盘常驻，图标随状态变化，一眼看出当前是显示还是隐藏
- **托盘左键单击 = 切换**（瞬时，比双击 exe 还快）；**右键 = 菜单**（开机自启 / 打开日志 / 关于 / 退出）
- 退出时如果图标还是隐藏状态，**自动恢复显示**，不会留下一个空荡荡的桌面
- 单实例运行：重复双击只通知常驻实例切换，不会启动第二个进程
- 开机自启可选，且自启时**不动图标**（只把托盘挂上），避免每次开机桌面就空一片
- 全程不需要管理员权限，不往 exe 所在目录写任何文件
- 找不到首选切换命令时**自动降级**到备用方案，并在「关于」里显示当前生效的方案
- 有日志可查，排障不用猜

---

## 系统要求

| | |
|---|---|
| **运行** | Windows 10 / 11（64 位）。**不需要**安装 Python，**不需要**管理员权限 |
| **构建** | Python ≥ 3.12 + [uv](https://docs.astral.sh/uv/)；构建原生启动器另需 MinGW-w64 的 `gcc` 与 `windres` |

实测环境：Windows 11（build 26200, 64 位）、Python 3.12.14、PyInstaller 6.22.3、
MinGW-w64 gcc 8.1.0。Windows 10 与更早版本未实测（所用机制自 Win7 起都通用）。

---

## 下载与安装

到 **[Releases 页面](https://github.com/npera2/hind_desktop_icons/releases/latest)**
下载 `DesktopIconToggle.exe`，放到任意目录，双击即可。

也可以在命令行下载：

```bash
gh release download --repo npera2/hind_desktop_icons --pattern DesktopIconToggle.exe
# 或者（无需 gh，直接走公开直链，latest 始终指向最新一次发布）
curl -L -o DesktopIconToggle.exe \
  https://github.com/npera2/hind_desktop_icons/releases/latest/download/DesktopIconToggle.exe
```

它是绿色单文件程序：不写注册表（除非你主动打开开机自启）、不装服务、不需要安装流程。

- **首次**双击会花一两秒把自己展开到 `%LOCALAPPDATA%\HindDesktopIcons\bin\<构建号>\`，只做一次
- **之后**每次双击都是秒回
- **卸载** = 删掉 `%LOCALAPPDATA%\HindDesktopIcons\` 整个目录；如果开过开机自启，
  先在托盘菜单里取消勾选

---

## 使用说明

### 托盘交互

| 图标 | 含义 |
|---|---|
| 蓝色 2×2 实心方块 | 桌面图标当前**可见** |
| 灰色方块 + 红斜杠 | 桌面图标当前**已隐藏** |

- **左键单击**托盘图标 → 隐藏 / 恢复（不启动任何进程，瞬时生效）
- **右键单击**托盘图标 → 菜单：
  - 隐藏 / 显示桌面图标（菜单首项，与左键单击等效）
  - 开机自动启动（可勾选，状态实时读注册表）
  - 打开日志
  - 关于（版本、当前状态、生效的切换方案、窗口句柄、日志路径）
  - 退出（若图标仍是隐藏状态，会先恢复再退出）

### 命令行参数

```
DesktopIconToggle.exe [选项]
```

| 参数 | 作用 |
|---|---|
| *(无)* | 启动常驻并切换一次；已有常驻实例时通知它切换（走原生快速路径） |
| `--toggle` | 只切换一次就退出，不要托盘（临时用一下） |
| `--no-start-toggle` | 只启动托盘，不改动图标（开机自启用） |
| `--quit` | 让已常驻的实例退出（会自动恢复图标） |
| `--status` | 打印当前状态、生效方案、窗口句柄、日志路径 |
| `--autostart on\|off\|status` | 开机自动启动开关 |
| `--debug` | 输出调试日志 |

`--status` 的输出示例：

```text
当前状态   : 图标已隐藏
切换方式   : WM_COMMAND 0x7402
系统开关   : HideIcons = True
桌面容器   : Progman (0x102A0)
DefView    : 0x102AE
图标控件   : 0x102B2
日志文件   : C:\Users\<你>\AppData\Local\HindDesktopIcons\app.log
```

### 数据与配置位置

程序不在自己的目录里留东西，所有运行期数据都在 `%LOCALAPPDATA%\HindDesktopIcons\`：

```
%LOCALAPPDATA%\HindDesktopIcons\
├── app.log            程序主体日志（UTF-8，上限 512 KB，滚动 3 份）
├── launcher.log       启动器日志（UTF-16LE，记录载荷展开等）
└── bin\
    └── <构建号>\      展开出来的程序主体；升级到新构建号时会自动清理旧目录
```

唯一的注册表写入是**可选的**开机自启项：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
下的 `HindDesktopIcons`（删掉它等同于关闭自启）。

---

## 工作原理

### 桌面图标是什么

桌面图标并不是独立程序画出来的，而是 `explorer.exe` 内部的一个 ListView 控件：

```
Progman / WorkerW          <- 桌面宿主窗口
  └─ SHELLDLL_DefView      <- 桌面视图容器
       └─ SysListView32    <- 真正绘制图标的控件
```

所以"隐藏桌面图标"不需要驱动、不需要注入，只要向正确的窗口发一条命令。

### 切换方案（按优先级自动降级）

| 优先级 | 做法 | 说明 |
|---|---|---|
| 1（默认） | 向 `SHELLDLL_DefView` 发送 `WM_COMMAND = 0x7402` | 等价于桌面右键「查看 → 显示桌面图标」。与系统菜单状态同步，explorer 重启后状态延续 |
| 2（兜底） | 对 `SysListView32` 调 `ShowWindow(SW_HIDE / SW_SHOW)` | 任何 Windows 版本都有效；代价是桌面右键菜单会失效、explorer 重启后会被重置 |
| 3（仅诊断） | 注册表 `HKCU\...\Explorer\Advanced\HideIcons` | 程序只读它作为诊断信息，不会主动改 |

方案 1 无效（少数精简系统、被第三方桌面工具接管）时会自动降级到方案 2，并把当前生效的方案
显示在「关于」里。

**状态判定统一看 `SysListView32` 的 `WS_VISIBLE`**——实测两种方案都会改变它，所以一个判据覆盖全部情况。
程序**不缓存自己的状态**，每次都读系统真实值，因此你手动改过状态之后，程序行为依然正确。

Windows 11 以及开着动态壁纸（Wallpaper Engine、Lively）时，`SHELLDLL_DefView` 可能挂在
`WorkerW` 而不是 `Progman` 下，因此查找窗口时会遍历所有顶层窗口兜底。

### 单文件 exe 的两层结构

```
DesktopIconToggle.exe                 ← 原生启动器（C 写，内嵌载荷）
  │
  ├─ 已有常驻实例
  │     → OpenEventW + SetEvent 通知它切换，然后退出（不启动任何子进程）
  │
  └─ 没有常驻实例
        → 首次把内嵌载荷展开到 %LOCALAPPDATA%\HindDesktopIcons\bin\<构建号>\
        → CreateProcess 启动它并把命令行参数原样转交，启动器自己退出
          │
          └─ 程序主体（PyInstaller onedir 载荷）
               ├─ 托盘常驻、菜单、单实例互斥体
               └─ 后台线程等待命名事件 → 收到就切换桌面图标
```

载荷在 exe 里是**不压缩**存放的自定义容器（`'HDIP'` 头 + 条目表 + 原始数据），
构建号取载荷内容的 SHA-256 前 8 位——载荷一变构建号就变，于是不会误用旧版本，
新版本会重新展开一次，并且展开成功后自动清掉其它构建号目录。

> 为什么不压缩：压缩省下的体积换不来 C 侧解压实现的复杂度与风险。展开约 16 MB
> 纯文件写入只需一秒多，比解压更省 CPU。

---

## 性能

单文件 exe 有个天然陷阱：`--onefile` 每次运行都要自解压。三种打包方式在本机的实测对比：

| 形态 | 体积 | 首次启动 | 之后每次启动 |
|---|---|---|---|
| PyInstaller `--onefile` | 11.1 MB | 7 s | **7 s** |
| PyInstaller `--onedir`（文件夹版） | 36 MB | 1.9 s | 0.7 s |
| **本项目：原生启动器 + 内嵌载荷** | **15.8 MB** | 1～5 s（含展开，只一次） | **0.6 s** |

其中"之后每次启动 0.6 秒"这一项值得说明：我又编了一个**不含载荷的 22 KB 空壳启动器**
走同一条代码路径，它也要 0.46～0.61 秒——也就是说这个量级是"在这台机器上启动任意进程"
的固定成本（开发环境的 I/O 拦截 + 杀软扫描），而不是 15.8 MB 载荷造成的（载荷只多贡献约 0.15 秒）。
真实环境下重复点击应该更快。

**如果你要频繁切换，用托盘左键单击**——它完全不启动新进程，是真正的瞬时。

---

## 从源码构建

### 环境依赖

| 用途 | 依赖 |
|---|---|
| 运行时（Python 库） | `pillow >= 12.3.0`、`pystray >= 0.19.5`（见 `pyproject.toml`） |
| 开发/打包 | `pyinstaller >= 6.22.3`（dev 依赖组） |
| 构建原生启动器 | MinGW-w64 的 `gcc` 与 `windres`，默认从 `C:\Program Files\mingw64\bin` 查找 |

Win32 调用全部用标准库 `ctypes` 直连，注册表用 `winreg`，**刻意不引入 `pywin32`**，
让最终产物没有额外的运行时依赖。

### 步骤

```bash
# 1. 准备环境（需要 uv：https://docs.astral.sh/uv/）
uv sync

# 2. 直接以源码方式运行
uv run python -m hind_desktop_icons --status
uv run python -m hind_desktop_icons --toggle

# 3.（可选）重新生成 exe 图标与托盘图标预览
uv run python tools/make_assets.py

# 4. ★ 构建最终的单文件 exe（含原生启动器）-> dist/DesktopIconToggle.exe
#    gcc / windres 会自动在 PATH 与常见目录（含 C:\msys64\mingw64\bin）中查找
uv run python tools/build_launcher.py
#    指定版本号（会写进 exe 的版本信息与安装目录名）：
uv run python tools/build_launcher.py --version 0.1.0
#    gcc / windres 不在默认位置时：
uv run python tools/build_launcher.py --gcc "D:\mingw64\bin\gcc.exe" --windres "D:\mingw64\bin\windres.exe"
#    只想复用已有载荷、只重编启动器：
uv run python tools/build_launcher.py --no-pyinstaller
```

> 注意别用 MSYS 目标的 gcc（`C:\msys64\usr\bin\gcc.exe`）：那样编出来的 exe 会依赖
> `msys-2.0.dll`，在普通 Windows 上根本无法运行。构建脚本会检测并拒绝这种路径。

`tools/build_launcher.py` 依次做这些事：
调 PyInstaller 打 onedir 载荷 → 用载荷内容哈希生成构建号 → 拼装容器 →
生成 `.rc`（图标 + 版本信息 + 载荷资源）→ `windres` 编译资源 → `gcc` 链接出单文件。

也可以只打纯 PyInstaller 版（不需要编译器）：

```bash
# 单文件版（每次启动都要自解压，约 7 秒）
uv run pyinstaller --noconfirm --clean \
    --workpath build/pyi --distpath dist packaging/DesktopIconToggle.spec

# 文件夹版（启动最快，但不是单文件）
HDI_ONEDIR=1 uv run pyinstaller --noconfirm --clean \
    --workpath build/pyi --distpath dist packaging/DesktopIconToggle.spec
```

### 目录结构

```text
.
├── pyproject.toml                  项目与依赖声明（含脚本入口 hind-desktop-icons）
├── uv.lock                         锁定依赖版本，保证构建可复现
├── .python-version                 约定的 Python 版本
├── README.md
├── LICENSE                         MIT 许可证
├── .github/workflows/release.yml   打 tag 自动构建并发布到 Releases
├── assets/
│   └── app.ico                     exe 图标（由 tools/make_assets.py 生成）
├── src/hind_desktop_icons/         程序主体（纯 Python）
│   ├── __init__.py                 对外只暴露 main()
│   ├── __main__.py                 python -m 入口
│   ├── desktop.py                ★ 核心：定位桌面窗口、切换显隐、读状态、自动降级
│   ├── ipc.py                      单实例互斥体 + 命名事件（跨进程通知）
│   ├── tray.py                     托盘图标、菜单、原生消息框
│   ├── autostart.py                开机自启（HKCU\...\Run）
│   ├── logs.py                     日志，并兜住 frozen 下为 None 的 stdout/stderr
│   └── cli.py                      命令行参数解析与启动分发
├── packaging/                      打包相关
│   ├── launcher.c                ★ 原生启动器源码（内嵌载荷 + 事件通知快速路径）
│   ├── DesktopIconToggle.spec      PyInstaller 配置（HDI_ONEDIR=1 切文件夹版）
│   ├── exe_entry.py                PyInstaller 打包入口（顶层脚本，避开相对导入的坑）
│   └── version_info.txt            纯 PyInstaller 版用的版本信息资源
├── tools/
│   ├── build_launcher.py           构建最终单文件 exe（PyInstaller + windres + gcc）
│   ├── make_assets.py              生成 ico 与托盘图标预览
│   └── verify_p0.py                机制验证脚本：确认本机 WM_COMMAND 方案是否可用
├── docs/
│   └── PLAN.md                     设计方案、逐项实测数据与踩坑记录
├── build/                          构建中间产物（已 gitignore）
└── dist/                           打包输出（已 gitignore，发布走 Releases）
```

---

## 常见问题

**图标不见了，但程序也没在运行？**
桌面空白处右键 →「查看」→ 勾上「显示桌面图标」。这是 Windows 自己的开关，任何时候都能用。
（如果你用任务管理器强杀了常驻进程，就会是这种情况——正常退出会自动恢复。）

**双击 exe 没反应？**
先看托盘有没有图标。如果你移动过 exe，而之前又开过开机自启，注册表里的路径还指向旧位置，
用 `--autostart off` 关掉再重新打开即可。

**首次双击为什么要等一两秒？**
启动器要把内嵌的约 16 MB 程序主体展开到 `%LOCALAPPDATA%`，只做一次。
之后每次双击都不再展开。`launcher.log` 里出现"载荷展开完成"说明这一步正常。

**杀软 / Windows Defender 报毒？**
单文件 exe 的常见误报。本项目已经关掉 UPX 压缩来降低误报率；如果仍被拦，
可以改用 Nuitka 构建，或给 exe 做代码签名。本仓库没有对二进制做签名，首次运行
可能看到 SmartScreen 的"未知发布者"提示。

**装了 Fences / 腾讯桌面整理之类？**
它们会接管桌面图标层，首选方案可能失效——程序会自动降级到 `ShowWindow` 方案，
`--status` 或「关于」里能看到当前生效的是哪一套。

**支持多显示器吗？**
支持，但桌面图标控件只有一个，所有显示器上的图标会**一起**隐藏/恢复，这是系统行为。

---

## 已知限制

- 只影响**桌面图标**，任务栏、开始菜单、通知区域不受影响
- 未做代码签名，首次运行会有 SmartScreen 提示
- 只在 Windows 11（build 26200）上实测过；Windows 10、动态壁纸环境（`WorkerW` 宿主）、
  非管理员账号、第三方桌面整理工具并存等场景**尚未实测**（代码里已按这些情况做了兜底）
- 如果系统既拒绝首选方案、也拒绝降级方案（极少数被策略锁定的精简系统），切换会失败并弹提示

---

## 设计文档

机制调研过程、逐项实测数据（含各种对照实验）以及踩过的坑，都记在
[`docs/PLAN.md`](docs/PLAN.md) 里。想改代码或排查兼容性问题，建议先读它。

---

## 发布新版本（维护者）

单文件 exe 通过 **GitHub Releases** 分发，**不提交进仓库**——15.8 MB 的二进制会迅速
撑大仓库历史，`.gitignore` 里已经忽略 `dist/`。

### 方式一：本地构建 + 手动发布（最直接）

```bash
# 1. 构建（自动查找 gcc / windres）
uv sync
uv run python tools/make_assets.py
uv run python tools/build_launcher.py --version 0.1.0

# 2. 打 tag 并推送
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0

# 3. 创建 Release 并把 exe 作为资产上传（需先 gh auth login）
gh release create v0.1.0 dist/DesktopIconToggle.exe \
  --title "DesktopIconToggle v0.1.0" \
  --generate-notes
```

上传完成后，`.../releases/latest/download/DesktopIconToggle.exe` 这条公开直链即可用。

### 方式二：自动化

仓库里的 [`.github/workflows/release.yml`](.github/workflows/release.yml) 会在推送 `v*`
标签时自动完成「构建 → 冒烟测试 → 发布」全流程，因此只需要：

```bash
git tag -a v0.1.1 -m "v0.1.1"
git push origin v0.1.1
```

也可以在 Actions 页面手动触发（`workflow_dispatch`）并填写版本号。

> 版本号要三处一致：`pyproject.toml` 的 `version`、tag 名（去掉开头的 `v`）、
> 以及 `tools/build_launcher.py --version` 传的值。exe 的安装目录名由
> 「版本号 + 载荷内容哈希」组成，所以换版本号会触发一次重新展开（这是有意为之的）。

---

## 许可

本项目采用 [MIT 许可证](LICENSE)。

