# 桌面图标一键隐藏 / 恢复工具 —— 实施方案

> 目标交付物：单个 `DesktopIconToggle.exe`，双击运行即可用；点一次隐藏所有桌面图标，再点一次恢复。

---

## 1. 目标与验收标准

| 编号 | 验收项 | 判定方式 |
|---|---|---|
| A1 | 双击 exe，桌面图标全部消失 | 目视桌面 |
| A2 | 再次双击同一个 exe，图标全部回来 | 目视桌面 |
| A3 | 全程无控制台黑窗、无窗口弹出 | 任务栏无新窗口 |
| A4 | 常驻后内存占用 < 40 MB，CPU 空闲约 0% | 任务管理器 |
| A5 | 无需管理员权限（不弹 UAC） | 普通用户双击 |
| A6 | 开机自启可开关 | 注册表 `HKCU\...\Run` |
| A7 | 资源管理器重启后状态不错乱 | 重启 explorer.exe 后复测 |
| A8 | 单文件 exe，体积 < 15 MB | 文件属性 |

---

## 2. 开发环境

- **操作系统**：Windows 10 21H2 及以上 / Windows 11（30 位不需要，只做 64 位）
- **Python**：3.12 或 3.13（`pyproject.toml` 已要求 `>=3.12`）。
  注意：用 Python 3.13 打包必须配 **PyInstaller ≥ 6.11**。
- **依赖管理**：uv（项目已有 `uv.lock`），`uv sync` / `uv run`
- **编辑器**：VS Code + Python 扩展
- **可选工具**：
  - Resource Hacker —— 后期手工改 exe 图标/版本信息
  - signtool（Windows SDK）—— 若有证书则签名，显著降低杀软误报
  - AutoHotkey v2 —— 做「机制验证对照实验」，一行命令即可验证切换命令 ID 是否有效

**运行时依赖清单（尽量少）**

| 包 | 用途 | 打包后体积贡献 |
|---|---|---|
| `pystray` | 系统托盘图标 + 菜单 | 小 |
| `pillow` | 运行时程序化绘制托盘图标（免外部资源文件） | ~3 MB |
| `pyinstaller`（dev） | 打包 | — |

不引入 `pywin32`：所有 Win32 API 用标准库 `ctypes` 直接调用，少一层依赖、体积更小。
注册表操作用标准库 `winreg`。**最终 exe 不依赖任何第三方安装环境。**

---

## 3. 目录结构（规划）

```
hind_desktop_icons/
├─ pyproject.toml
├─ README.md
├─ docs/PLAN.md                  # 本文档
├─ assets/app.ico                # 仅用于 exe 文件图标
├─ build/ dist/                  # PyInstaller 产物（gitignore）
├─ DesktopIconToggle.spec        # 固化的打包配置
└─ src/hind_desktop_icons/
   ├─ __init__.py                # 保留 main() 入口（pyproject 的脚本入口）
   ├─ __main__.py                # python -m / PyInstaller 的真正入口
   ├─ cli.py                     # 参数解析与启动分发
   ├─ desktop.py                 # ★ 核心：定位桌面 ListView、切换显隐、读状态
   ├─ ipc.py                     # 单实例互斥体 + 命名事件（二次双击通知）
   ├─ tray.py                    # 托盘图标、菜单、双状态图标绘制
   ├─ autostart.py               # 开机自启（HKCU\...\Run）
   └─ logs.py                    # 日志重定向到 %LOCALAPPDATA%
```

---

## 4. 核心机制：怎么隐藏 / 恢复桌面图标

### 4.1 桌面图标的窗口结构

桌面图标**不是**一个独立程序画的，而是 `explorer.exe` 里的一个 ListView 控件。层级关系：

```
桌面（Progman 窗口）
└─ SHELLDLL_DefView        ← 桌面视图容器，切换显隐的命令就发给它
   └─ SysListView32        ← 真正显示图标列表的控件
```

Windows 11 及部分场景（开启了动态壁纸 / 第三方壁纸工具）下，`SHELLDLL_DefView` 不再挂在
`Progman` 下，而是被挂在某个 `WorkerW` 顶层窗口下。**因此查找必须做两级兜底，不能只找 Progman。**

### 4.2 三套方案与优先级

| 优先级 | 方案 | 原理 | 优点 | 缺点 |
|---|---|---|---|---|
| **A（首选）** | 向 `SHELLDLL_DefView` 发送 `WM_COMMAND`，命令 ID `0x7402` | 等价于点桌面右键菜单「查看 → 显示桌面图标」 | 与系统菜单**状态同步**；图标只是隐藏，桌面右键菜单仍可用；explorer 重启后状态延续 | 极少数精简系统/魔改壳可能无效，需自检降级 |
| **B（兜底）** | 对 `SysListView32` 调 `ShowWindow(SW_HIDE / SW_SHOW)` | 直接把控件藏起来 | 任何版本都有效 | 桌面右键菜单会失效；explorer 重启后恢复显示；状态不由系统维护 |
| **C（最后手段）** | 写注册表 `HKCU\...\Explorer\Advanced\HideIcons` 再广播刷新 | 改系统开关本身 | 最"正规" | 需要刷新 explorer 才生效，体验差；仅当 A、B 都失败时提示用户 |

**实现策略：以 A 为主路径，启动时做一次自检；A 无效则自动降级到 B，并把所选方案写入日志。**

### 4.3 关键代码骨架

```python
# desktop.py 节选
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_COMMAND = 0x0111
CMD_TOGGLE_DESKTOP_ICONS = 0x7402   # 与右键菜单「显示桌面图标」同一命令


def _enum_child(parent: int, cls: str) -> int:
    return user32.FindWindowExW(wintypes.HWND(parent), 0, cls, None)


def find_def_view() -> int:
    """返回 SHELLDLL_DefView 句柄；兼容 Progman / WorkerW 两种宿主。"""
    defview = _enum_child(user32.FindWindowW("Progman", None), "SHELLDLL_DefView")
    if defview:
        return defview
    hits = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        child = _enum_child(hwnd, "SHELLDLL_DefView")
        if child:
            hits.append(child)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return hits[0] if hits else 0


def find_listview() -> int:
    defview = find_def_view()
    return _enum_child(defview, "SysListView32") if defview else 0


def toggle_via_command() -> bool:
    defview = find_def_view()
    if not defview:
        return False
    user32.SendMessageW(wintypes.HWND(defview), WM_COMMAND,
                        wintypes.WPARAM(CMD_TOGGLE_DESKTOP_ICONS), 0)
    return True


def set_visible_fallback(visible: bool) -> bool:
    lv = find_listview()
    if not lv:
        return False
    user32.ShowWindow(wintypes.HWND(lv), 5 if visible else 0)   # SW_SHOW / SW_HIDE
    return True
```

**状态读取（用于决定"切还是切回来"）**

```python
import winreg

ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"

def icons_hidden_registry() -> bool | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ADV) as k:
            v, _ = winreg.QueryValueEx(k, "HideIcons")
            return bool(v)
    except FileNotFoundError:
        return False          # 键不存在 = 图标显示中
```

- 系统口径：`HideIcons = 1` 表示图标已隐藏。该值由 explorer 在切换时自己维护，
  **所以方案 A 成功后无需我们写注册表**，只要读它就能知道当前状态。
- 若走方案 B（ShowWindow），注册表不会变，此时用 `IsWindowVisible(listview_hwnd)` 判状态。
- 混淆点处理：切换动作发出后等 150–250 ms 再复读一次状态做校验；两次读数不一致则
  以 `IsWindowVisible` 为准，并切换内部降级标记。

### 4.4 「点击一次隐藏，再次点击恢复」的状态语义

- 程序的唯一状态变量就是**图标当前是否可见**，每次"激活"都做一次 **取反（toggle）**。
- 不缓存、不持久化自己的状态——始终以系统真实状态为准，这样即使中途用户手动右键切换过，
  程序行为仍然正确（这一点很容易被忽略，但它是"不会越点越乱"的关键）。

---

## 5. 程序入口与运行流程

### 5.1 入口

- `pyproject.toml` 的脚本入口保持 `hind_desktop_icons:main`
- `src/hind_desktop_icons/__main__.py` → 调 `cli.main()`
- PyInstaller 以 `__main__.py` 为打包入口

### 5.2 交互模式（推荐：混合模式）

| 模式 | 行为 | 评价 |
|---|---|---|
| 纯瞬时 | 双击 → 切换一次 → 立即退出 | 最轻量，但无状态反馈、无处退出 |
| 纯托盘 | 双击 → 常驻托盘 → 单击托盘图标切换 | 状态可见，但"双击 exe 即隐藏"的直觉被打断 |
| **混合（推荐）** | 首次双击 = 启动托盘 + **立即隐藏**；再次双击同一个 exe = 通知已常驻的实例切换一次，新进程自己退出 | 完全对上你的描述："点一次隐藏，再点一次恢复"，同时保留托盘作为状态灯和退出入口 |

### 5.3 运行流程（混合模式）

```
双击 DesktopIconToggle.exe
        │
        ├─ 解析参数（默认：启动并切换）
        │
        ├─ 创建命名互斥体 Local\HindDesktopIconsToggle
        │        │
        │        ├─ 已存在 → 说明有实例在跑
        │        │      → SetEvent(Local\HindDesktopIconsToggleEvt) 通知它切换
        │        │      → 本进程立即退出（毫秒级，无窗口闪现）
        │        │
        │        └─ 创建成功 → 首次启动
        │               ├─ 初始化日志（%LOCALAPPDATA%\HindDesktopIcons\app.log）
        │               ├─ 自检：定位 DefView + 试切换一次并校验
        │               ├─ 启动托盘图标（按当前状态选图标）
        │               ├─ 启动后台线程 WaitForSingleObject(Evt)
        │               │      └─ 收到信号 → 切换 + 刷新托盘图标
        │               ├─ 立即执行一次切换（隐藏图标）
        │               └─ icon.run() 进入消息循环
```

**单实例与 IPC 的两个细节**

- 命名对象用 `Local\` 前缀，**不要用 `Global\`**：`Global\` 需要 `SeCreateGlobalPrivilege`，
  普通用户账号下会创建失败（管理员下能跑、普通用户下失效，是最典型的"我这儿好好的"翻车点）。
- IPC 用**命名事件**而不是自建隐藏窗口类：代码量小、无消息循环依赖，跨进程够用。

```python
# ipc.py 节选
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
MUTEX = "Local\\HindDesktopIconsToggle"
EVENT = "Local\\HindDesktopIconsToggleEvt"

def acquire_singleton() -> tuple[bool, int]:
    h = kernel32.CreateMutexW(None, False, MUTEX)
    first = ctypes.get_last_error() != 183        # ERROR_ALREADY_EXISTS
    return first, h

def signal_existing() -> None:
    kernel32.SetEvent(kernel32.CreateEventW(None, False, False, EVENT))

def wait_for_signal(stop_check, on_signal) -> None:
    evt = kernel32.CreateEventW(None, False, False, EVENT)
    while True:
        rc = kernel32.WaitForSingleObject(evt, 500)
        if rc == 0:
            on_signal()
        if stop_check():
            return
```

### 5.4 命令行参数（便于做快捷方式、开机自启、快捷排障）

| 参数 | 作用 |
|---|---|
| 无 | 启动 + 切换（默认，符合"双击即用"） |
| `--no-start-toggle` | 只启动托盘，不动图标（配合开机自启用） |
| `--toggle` | 只切换一次然后退出，不常驻（纯瞬时模式） |
| `--quit` | 通知常驻实例退出 |
| `--autostart on\|off` | 写 / 删 `HKCU\...\Run` 项 |
| `--status` | 打印当前状态（stdout，供排障） |

---

## 6. 状态切换与界面交互（托盘）

- **托盘图标 = 唯一的界面**，不创建任何窗口。用 Pillow 在内存里画两套图标，
  **不打包外部 png/ico 资源**（省掉 `sys._MEIPASS` 路径处理的坑）：
  - 图标显示中 → 实心 2×2 小方块（表示"桌面上有图标"）
  - 图标已隐藏 → 同样的方块但灰化 + 一条斜线
- **右键菜单**：

  | 菜单项 | 说明 |
  |---|---|
  | 切换桌面图标（默认项） | `default=True`，左键单击托盘即触发 |
  | 开机自动启动 | 复选项，勾选状态从注册表实时读取 |
  | 打开日志 | 用系统默认程序打开日志文件 |
  | 关于 | 版本 + 当前所用方案（A/B/C），排障用 |
  | 退出 | `icon.stop()` + 释放互斥体 |

- **触发方式**：把"切换"设为 pystray 的默认菜单项（`MenuItem(..., default=True)`）。
  Windows 下左键单击托盘图标即执行该默认项，右键弹菜单。
  若实测单击无响应（pystray 各版本行为有差异），则在托盘通知回调里自行处理
  `WM_LBUTTONUP` / `WM_LBUTTONDBLCLK`，这是可控的降级点。
- **反馈**：切换后立刻更新 `icon.icon` 与 `icon.title`（悬停提示如"桌面图标：已隐藏"），
  这样操作结果不用回桌面确认。

---

## 7. 打包成单个 exe

### 7.1 推荐：PyInstaller

```bash
uv add --dev pyinstaller
uv run pyinstaller --noconfirm --clean --onefile --windowed \
  --name DesktopIconToggle \
  --icon assets/app.ico \
  --noupx \
  --exclude-module tkinter \
  --exclude-module unittest \
  --version-file version_info.txt \
  src/hind_desktop_icons/__main__.py
```

产物：`dist/DesktopIconToggle.exe`，约 10–13 MB。

固化参数到 `DesktopIconToggle.spec`，之后一条 `uv run pyinstaller DesktopIconToggle.spec` 复现。
建议在 `pyproject.toml` 里加脚本别名（如 `uv run build`）以便重复构建。

### 7.2 备选：Nuitka

```bash
uv run nuitka --onefile --windows-console-mode=disable \
  --windows-icon-from-ico=assets/app.ico \
  --output-filename=DesktopIconToggle.exe \
  src/hind_desktop_icons/__main__.py
```

优点：启动快（无 onefile 解压开销）、杀软误报明显更少、体积更小。
缺点：需本机有 MSVC 或 MinGW 编译器，构建时间从秒级变成分钟级。

**建议：先用 PyInstaller 跑通交付；如果被 Defender/360 拦，再切 Nuitka。**

### 7.3 打包注意事项（逐条都是真实会踩的）

1. **`--windowed` 下 `sys.stdout` / `sys.stderr` 是 `None`**，任何 `print()` 都会抛异常并静静崩掉。
   必须：全部改用 `logging` 写到 `%LOCALAPPDATA%\HindDesktopIcons\app.log`，
   并在入口最前面加保护：
   ```python
   if sys.stdout is None:
       sys.stdout = open(os.devnull, "w", encoding="utf-8")
   if sys.stderr is None:
       sys.stderr = open(os.devnull, "w", encoding="utf-8")
   ```
2. **日志/配置一律写 `%LOCALAPPDATA%`，绝不写 exe 同目录**。exe 常被放进桌面、Program Files、
   甚至 OneDrive 同步目录，那里可能无写权限或会产生同步垃圾。
3. **onefile 首启动慢 1–2 秒**（解压到 `%TEMP%\_MEIxxxx`）。想缓解可加 `--splash`（会捆绑 tkinter，
   体积增大），或者接受这个延迟——用户场景是偶尔点一下，1 秒可接受。
4. **杀软误报**：PyInstaller onefile 是误报重灾区，尤其国内安全软件。缓解顺序：
   ① 加 `--noupx`，② 用 Nuitka，③ 代码签名，④ 向厂商提交白名单。
5. **不要加 `--uac-admin`**：本程序只碰 `HKCU`，全程不需要管理员权限。
6. **图标文件必须提供 `.ico` 多尺寸格式**（16/32/48/256），只给 png 会让资源管理器里图标糊。
7. **`multiprocessing.freeze_support()`**：本程序用线程不用多进程，但入口加一行无害，
   可避免未来引入进程池时在 frozen 环境无限 fork。
8. **首次运行 SmartScreen 提示"未知发布者"**：单文件 exe 无签名的必然结果，属预期，需在说明里告知。
9. 构建环境与实际运行系统位数一致（都 64 位）；不建议交叉编译。

---

## 8. 注意事项与已知坑

| 坑 | 说明与对策 |
|---|---|
| Win11 桌面宿主变化 | Win11 / 开启动态壁纸后 `SHELLDLL_DefView` 挂在 `WorkerW` 下。**必须 `EnumWindows` 遍历兜底**，不能写死 Progman |
| 多个 `SHELLDLL_DefView` | Wallpaper Engine / Lively 等会额外造 WorkerW。取第一个持有 `SysListView32` 的那个，并记录日志 |
| 第三方桌面整理工具 | Fences、腾讯桌面整理等会接管桌面图标层，方案 A 可能无效 → 自动降级 B，并在"关于"里提示冲突 |
| 多显示器 | 桌面 ListView 只有一个，所有显示器上的图标会**一起**隐藏，这是系统行为，不用特殊处理 |
| 缩放/分辨率 | 本方案与 DPI、分辨率无关（不涉及坐标计算），不会触发图标重排 |
| explorer 重启 | 方案 A 下状态由系统延续；方案 B 下图标会自己回来 → 托盘状态需在切换后以系统为准刷新 |
| Win+D / 显示桌面 | 互不影响，无需处理 |
| 组策略/精简系统 | 极少数环境桌面命令被禁用，依赖 C 方案 + 明确报错提示 |
| 任务栏/开始菜单图标 | 本工具**只影响桌面图标**，"所有桌面图标"按此口径实现 |

---

## 9. 实施步骤（分阶段，每阶段可验收）

| 阶段 | 内容 | 产出 / 验收 |
|---|---|---|
| **P0 机制验证（0.5 天）** | 先用 AutoHotkey 或一次性 Python 脚本验证 `0x7402` 命令在本机 Win10/11 上是否有效；验证 DefView 查找链 | 一条命令能隐藏、再一条能恢复 |
| **P1 核心库（0.5 天）** | 写 `desktop.py`：查找 + 切换 + 读状态 + 降级自检；附 `--status` 调试 | 命令行能稳定反复切换 20 次不错乱 |
| **P2 单实例 + IPC（0.5 天）** | `ipc.py`：互斥体、命名事件、二次双击通知 | 连点 5 次 exe，状态交替正确、无残留进程 |
| **P3 托盘界面（0.5 天）** | `tray.py`：双状态图标、菜单、开机自启开关 | 托盘图标随状态变化，退出后互斥体释放 |
| **P4 日志与健壮性（0.5 天）** | `logs.py`、异常兜底、无 stdout 保护 | 删掉图标/找不到窗口时不崩，日志有记录 |
| **P5 打包（0.5 天）** | spec 固化、图标、版本信息、体积优化 | `dist/DesktopIconToggle.exe` 单文件可跑 |
| **P6 兼容性测试** | Win10 21H2/22H2、Win11 22H2/23H2/24H2；开/关动态壁纸；多显示器 | 全部通过 A1–A8 |
| **P7 体验收尾** | 快捷方式说明、开机自启、README 使用说明 | 交付 |

---

## 10. 测试清单（交付前必跑）

1. 桌面有图标 → 双击 exe → 全部隐藏；再双击 → 全部恢复（重复 20 轮）
2. 隐藏状态下手动右键桌面 → 勾选状态应与实际一致（验证方案 A 的状态同步）
3. 隐藏状态下重启 `explorer.exe` → 图标是否仍隐藏？托盘图标状态是否仍正确？
4. 托盘右键 → 退出 → 再双击 exe → 能正常重新启动（互斥体已释放）
5. 开着 Fences / 动态壁纸 → 是否自动降级并仍可用
6. 多显示器下切换，两个屏幕的图标都正确响应
7. 任务管理器确认：无控制台窗口、内存 < 40 MB、空闲 CPU ≈ 0%
8. 普通用户账号（非管理员）双击运行不弹 UAC
9. 开机自启开关勾选/取消后，注册表 `HKCU\...\Run` 项同步增删
10. 把 exe 放到 Program Files 或只读目录下运行，日志仍能正常写入

---

## 11. 实施结果（2026-10-16，P0–P5 已全部落地）

### 11.1 P0 机制验证实测（本机 Windows + Python 3.12）

| 验证项 | 结果 |
|---|---|
| `SHELLDLL_DefView` 查找 | 成功，本机挂在 `Progman` 下（单实例），`WorkerW` 分支保留为兜底 |
| `WM_COMMAND = 0x7402` 发往 DefView | **有效**：`HideIcons` False→True，`SysListView32` 的 `WS_VISIBLE` True→False |
| 两种状态读法一致性 | **完全同步**，所以统一用 `IsWindowVisible(ListView)` 一个判据即可 |
| `ShowWindow` 降级 | 有效（`SW_HIDE`/`SW_SHOW` 都能改变 ListView 可见性） |
| 状态还原 | 正常，无残留

结论：方案 A 作为主路径成立，方案 B 可作为真实降级路径。

### 11.2 交付物

| 产物 | 大小 | 说明 |
|---|---|---|
| `dist/DesktopIconToggle.exe` | 11.1 MB | 单文件，双击即用 |
| `dist/DesktopIconToggle-portable/` | 36.5 MB | 文件夹版，启动快（见 11.3） |

### 11.3 启动耗时实测 —— 唯一的遗留问题

| 形态 | 首次 | 后续每次 |
|---|---|---|
| **单文件 onefile** | ≈ 7 s | ≈ 7 s |
| **文件夹 onedir** | 1.9 s | **0.7 s** |
| 源码方式（uv run） | 1.5 s | 1.5 s |

原因：PyInstaller 单文件每次运行都要把约 36 MB 内容解压到 `%TEMP%\_MEIxxxx`
（约 150 个文件），并逐个被杀软实时扫描；这段开销全部发生在 Python 代码运行**之前**，
程序内部优化无法改善。日志时间戳可证：进程启动到"日志初始化完成"之间整整 7 秒。

对交互的影响：本方案的"再双击一次 exe"= 启动一个新进程 = 每次都要付这个启动成本。
托盘左键单击则是常驻进程内的事件，瞬时。

> 注：实测环境带有 I/O 拦截（裸读 11 MB 耗时 0.69 s，约 16 MB/s，明显低于 NVMe 正常水平），
> 真实双击的耗时可能低于上表，需要在实际双击场景下复核。

### 11.4 已实测通过的验收项

- A1/A2 双击隐藏、再双击恢复 —— 连续 5 轮交替正确
- A3 无控制台（PE 子系统 = 2，GUI），分离进程运行无 `stdout is None` 崩溃
- A5 全程无需管理员权限
- A6 开机自启注册表读写正常，测试后已清除、无残留
- A8 单文件 11.1 MB（通过剔除 Pillow 的 AVIF/WebP/CMS/字体插件，从 16.0 MB 降下来）
- 单实例：退出后互斥体正确释放，可重新启动
- 退出时若图标仍隐藏，会自动恢复显示
- 打包后版本信息资源正确（"桌面图标开关 v0.1.0.0"）

### 11.5 尚未实测

- explorer.exe 重启后的状态延续（需要打断用户当前桌面，未执行）
- Win10 / 其它 Win11 版本、动态壁纸（WorkerW 宿主）场景
- 多显示器、第三方桌面整理工具（Fences 等）
- 普通用户账号（非管理员）下的运行

---

## 12. 追加改造：原生启动器（同日晚，用户选定"方案 2"）

### 12.1 问题

11.3 实测：单文件 onefile 每次启动约 7 秒，其中约 6 秒是自解压。而本方案的交互是
"反复点同一个 exe"，于是每次点击都要付这 6 秒。对照实验：同一份代码 onedir 只要 0.7 秒。

### 12.2 方案

用 C 写一个不含 Python 的原生启动器（`packaging/launcher.c`），内嵌 onedir 载荷：

```
DesktopIconToggle.exe（启动器 = 代码 + 内嵌载荷，15.8 MB）
  ├─ 无参数/--toggle/--quit 且已有常驻实例
  │     → OpenEventW + SetEvent 通知它，立刻返回（不启动任何子进程）
  └─ 其它情况
        → 首次把载荷展开到 %LOCALAPPDATA%\HindDesktopIcons\bin\<构建号>\
        → CreateProcess 载荷并把参数原样转交，启动器自己退出
```

- 载荷容器是自定义格式（`'HDIP'` + 条目表 + 原始数据），**不压缩**：
  本机 MinGW 没有 zlib，且压缩省下的体积换不来 C 侧的解压复杂度与风险。
- 构建号 = 载荷内容 SHA-256 前 8 位（`0.1.0-3fd2da52`）。载荷一变构建号就变，
  于是旧版本不会被误用，新版本会重新展开一次；展开成功后自动清掉其它构建号目录。
- 标记文件 `.installed` **最后写**：它存在就代表整套文件都写完了，被中断的安装下次重来。
- 用命名互斥体 `Local\HindDesktopIconsInstall` 防止两个实例同时展开。

### 12.3 实测结果

| 操作 | 耗时 |
|---|---|
| 首次双击（展开 15.8 MB + 清理旧版本 + 启动托盘 + 隐藏） | 5.46 s |
| 首次双击（已展开、无清理） | 0.87 s |
| **之后每次双击（原生事件通知）** | **0.59 ~ 0.80 s** |
| 对照：22 KB 空壳原生 exe 走同一条路径 | 0.46 ~ 0.61 s |
| 对照：PyInstaller onefile 版 | 7 s |

关键读数：**22 KB 的空壳 exe 也要 0.5 秒**，说明这个耗时是"启动任意进程"的固定成本
（本机沙箱的 I/O 拦截 + 杀软扫描），与 15.8 MB 的载荷关系不大（只差约 0.15 秒）。
换句话说快速路径已经压到物理下限；真实环境中理应更快。

其余验证：GUI 子系统（双击不弹黑窗）、版本信息资源正确、`--status` 输出能转发到终端、
`--quit` 自动恢复图标、退出后无残留进程、旧构建号目录被自动清理。

### 12.4 踩坑记录

1. **`urllib` 不能剔**。为了瘦身曾把 `urllib` 加进 excludes，结果打出来的程序一启动就崩：
   PyInstaller 的运行时钩子 `pyi_rth_inspect` → `zipfile` → `pathlib` → `urllib.parse`
   是冻结点启动的必经路径。最终只在 excludes 里保留确定安全的项。
2. **9.1 MB 是被 TLS 连带拖进来的**。`libcrypto-3-x64.dll`(7.6 MB) + `libssl-3-x64.dll`(1.5 MB)
   本程序完全用不到；剔掉 `ssl`/`_ssl`/`_hashlib` 模块并在 spec 里过滤 `a.binaries`
   之后载荷从 27 MB 降到 15.8 MB。
   （`urllib.request` 是 `try: import ssl / except ImportError` 写法，不受影响。）
3. **windres 栽在路径空格上**。`C:\Program Files\...` 让它的预处理器命令行断成 `'C:\Program'`。
   解法：把 mingw 的 bin 加进 PATH 用裸名 `gcc` 调起，并显式补
   `--preprocessor-arg=-E -xc-header -DRC_INVOKED`（只给 `--preprocessor` 会丢掉 `-E`，
   gcc 会把 .rc 当源码去链接）。
4. **C 里字符串字面量不能与 `static const` 变量拼接**，那个位置必须是宏
   （`L"\\bin\\" HDI_BUILD_ID` 可以，`L"\\" APP_DIRNAME` 不行）。
5. 需要 `-DUNICODE -D_UNICODE`，否则 `RT_RCDATA` 是窄字符版，`FindResourceW` 类型不匹配。

### 12.5 交付物

`dist/DesktopIconToggle.exe`（15.81 MB，单文件）。纯 PyInstaller 的 onefile 版
（11.1 MB / 每次启动 7 秒）移到了 `build/archive/` 作对照，可随时删除。


