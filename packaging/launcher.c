/*
 * DesktopIconToggle 原生启动器
 * ============================
 *
 * 为什么需要它
 * ------------
 * PyInstaller 的单文件 exe 每次运行都要把自己解压到 %TEMP%（本机实测约 7 秒），
 * 而本程序的交互是"反复点同一个 exe"，于是每次点击都要付这个成本。
 *
 * 这个启动器用 C 写，不含 Python：
 *
 *   1. 内嵌 onedir 载荷（不压缩，容器格式见下），首次运行解压到
 *      %LOCALAPPDATA%\HindDesktopIcons\bin\<构建号>\，只做一次；
 *   2. 已有常驻实例时**完全不启动任何子进程**，直接用命名事件通知它切换
 *      （OpenEventW + SetEvent，约 30 毫秒）——这是"再点一次就恢复"能秒回的关键；
 *   3. 其余情况把参数原样转交给已解压的载荷程序（约 0.7 秒启动）。
 *
 * 命名事件名必须与 src/hind_desktop_icons/ipc.py 保持一致。
 *
 * 容器格式（全部小端）
 * --------------------
 *   0  : 'H','D','I','P'
 *   4  : u32 格式版本 = 1
 *   8  : u32 条目数
 *   12 : 条目表，每条 12 字节头 + 名字：
 *          u16 名字长度（字节）
 *          u16 保留（0）
 *          u32 数据大小
 *          u32 数据偏移（相对容器起点）
 *          u8  名字（UTF-8，路径分隔用 '/'）
 *        之后紧跟各文件数据块
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

#define IDR_PAYLOAD        1001
#define CONTAINER_MAGIC    0x50494448u /* 'HDIP' */
#define CONTAINER_VERSION  1u

#ifndef HDI_BUILD_ID
#define HDI_BUILD_ID "dev"
#endif

#define PATH_CAP  1024
#define CMD_CAP   4096
#define LOG_CAP   1024

/* 与 ipc.py 对齐 */
static const WCHAR EVENT_TOGGLE[]  = L"Local\\HindDesktopIconsToggleEvt";
static const WCHAR EVENT_QUIT[]    = L"Local\\HindDesktopIconsQuitEvt";
static const WCHAR MUTEX_INSTALL[] = L"Local\\HindDesktopIconsInstall";

static const WCHAR APP_DIRNAME[]  = L"HindDesktopIcons";
static const WCHAR PAYLOAD_EXE[]  = L"DesktopIconToggle.exe";
static const WCHAR MARKER_NAME[]  = L".installed";

/* ------------------------------------------------------------------ 小工具 */

static unsigned int rd_u32(const unsigned char *p)
{
    return (unsigned int)p[0] | ((unsigned int)p[1] << 8) |
           ((unsigned int)p[2] << 16) | ((unsigned int)p[3] << 24);
}

static unsigned int rd_u16(const unsigned char *p)
{
    return (unsigned int)p[0] | ((unsigned int)p[1] << 8);
}

static void str_copy(WCHAR *dst, size_t cap, const WCHAR *src)
{
    if (cap == 0) return;
    lstrcpynW(dst, src, (int)cap);
}

static void str_append(WCHAR *dst, size_t cap, const WCHAR *src)
{
    size_t used = (size_t)lstrlenW(dst);
    if (used + 1 >= cap) return;
    lstrcpynW(dst + used, src, (int)(cap - used));
}

/* 逐级创建目录；成功或已存在都算成功 */
static BOOL ensure_dir(const WCHAR *path)
{
    WCHAR tmp[MAX_PATH * 2];
    WCHAR *p;

    str_copy(tmp, PATH_CAP, path);
    for (p = tmp; *p; ++p) {
        if (*p != L'\\' && *p != L'/') continue;
        if (p == tmp || p[-1] == L':') continue;  /* 前导分隔符与盘符根 */
        *p = 0;
        CreateDirectoryW(tmp, NULL);
        *p = L'\\';
    }
    if (CreateDirectoryW(tmp, NULL)) return TRUE;
    return GetLastError() == ERROR_ALREADY_EXISTS;
}

static BOOL write_file(const WCHAR *path, const unsigned char *data, unsigned int size)
{
    HANDLE h;
    DWORD written = 0;
    unsigned int total = 0;

    h = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                    FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return FALSE;

    while (total < size) {
        DWORD chunk = (size - total) > (1u << 20) ? (1u << 20) : (size - total);
        if (!WriteFile(h, data + total, chunk, &written, NULL) || written == 0) {
            CloseHandle(h);
            return FALSE;
        }
        total += written;
    }
    CloseHandle(h);
    return TRUE;
}

/* 追加一行到 %LOCALAPPDATA%\HindDesktopIcons\launcher.log，排障用 */
static void log_text(const WCHAR *appdir, const WCHAR *text, DWORD code)
{
    WCHAR path[MAX_PATH * 2];
    WCHAR line[LOG_CAP];
    WCHAR num[32];
    DWORD written = 0;
    HANDLE h;

    if (!ensure_dir(appdir)) return;

    str_copy(path, PATH_CAP, appdir);
    str_append(path, PATH_CAP, L"\\launcher.log");

    str_copy(line, LOG_CAP, text);
    if (code != 0) {
        wsprintfW(num, L"（错误码 %u）", (unsigned int)code);
        str_append(line, LOG_CAP, num);
    }
    str_append(line, LOG_CAP, L"\r\n");

    h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL,
                    OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    WriteFile(h, line, (DWORD)(lstrlenW(line) * sizeof(WCHAR)), &written, NULL);
    CloseHandle(h);
}

static BOOL utf8_to_wide(const unsigned char *src, unsigned int len, WCHAR *dst, int cap)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, (const char *)src, (int)len, dst, cap - 1);
    if (n <= 0) return FALSE;
    dst[n] = 0;
    return TRUE;
}

static BOOL file_exists(const WCHAR *path)
{
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

/* -------------------------------------------------------------- 载荷容器 */

typedef struct {
    const unsigned char *base;
    const unsigned char *cursor;
    const unsigned char *end;
    unsigned int remaining;
} Container;

typedef struct {
    const unsigned char *name;
    unsigned int name_len;
    unsigned int size;
    unsigned int offset;
} Entry;

static BOOL container_open(const unsigned char *data, unsigned int len, Container *c)
{
    if (!data || len < 12) return FALSE;
    if (rd_u32(data) != CONTAINER_MAGIC) return FALSE;
    if (rd_u32(data + 4) != CONTAINER_VERSION) return FALSE;

    c->base = data;
    c->cursor = data + 12;
    c->end = data + len;
    c->remaining = rd_u32(data + 8);
    return TRUE;
}

static BOOL container_next(Container *c, Entry *e)
{
    if (c->remaining == 0) return FALSE;
    if (c->cursor + 12 > c->end) return FALSE;

    e->name_len = rd_u16(c->cursor);
    e->size = rd_u32(c->cursor + 4);
    e->offset = rd_u32(c->cursor + 8);
    e->name = c->cursor + 12;

    c->cursor += 12 + e->name_len;
    if (c->cursor > c->end) return FALSE;
    c->remaining--;
    return TRUE;
}

/* 把容器里的所有文件写到目标目录，返回成功写出的文件数；失败返回 -1 */
static int install_payload(Container src, const WCHAR *dir)
{
    Container c = src;
    Entry e;
    int count = 0;

    while (container_next(&c, &e)) {
        WCHAR rel[PATH_CAP];
        WCHAR full[MAX_PATH * 2];
        WCHAR *q;
        WCHAR *slash;

        if (!utf8_to_wide(e.name, e.name_len, rel, PATH_CAP)) return -1;
        for (q = rel; *q; ++q) {
            if (*q == L'/') *q = L'\\';
        }

        str_copy(full, PATH_CAP, dir);
        str_append(full, PATH_CAP, L"\\");
        str_append(full, PATH_CAP, rel);

        slash = wcsrchr(full, L'\\');
        if (slash) {
            *slash = 0;
            if (!ensure_dir(full)) return -1;
            *slash = L'\\';
        }

        if (e.offset + e.size > (unsigned int)(c.end - c.base)) return -1;
        if (!write_file(full, c.base + e.offset, e.size)) return -1;
        count++;
    }
    return count;
}

/* ---------------------------------------------------------- 事件与启动 */

static BOOL signal_event(const WCHAR *name)
{
    HANDLE h = OpenEventW(EVENT_MODIFY_STATE, FALSE, name);
    BOOL ok;
    if (!h) return FALSE;
    ok = SetEvent(h);
    CloseHandle(h);
    return ok;
}

static BOOL launch_payload(const WCHAR *exe, const WCHAR *tail)
{
    WCHAR cmd[CMD_CAP];
    WCHAR workdir[MAX_PATH * 2];
    WCHAR *slash;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    /* 带参数时才让子进程继承控制台句柄：这样 `--status` 的输出在终端里看得见；
     * 不带参数（双击）时托盘会长期驻留，继承句柄会让调用方的管道一直不关闭。 */
    BOOL inherit = (*tail != 0);

    str_copy(cmd, CMD_CAP, L"\"");
    str_append(cmd, CMD_CAP, exe);
    str_append(cmd, CMD_CAP, L"\"");
    if (tail && *tail) {
        str_append(cmd, CMD_CAP, L" ");
        str_append(cmd, CMD_CAP, tail);
    }

    str_copy(workdir, PATH_CAP, exe);
    slash = wcsrchr(workdir, L'\\');
    if (slash) *slash = 0;

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    if (!CreateProcessW(exe, cmd, NULL, NULL, inherit, 0, NULL, workdir, &si, &pi))
        return FALSE;

    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return TRUE;
}

/* -------------------------------------------------------------- 参数解析 */

static const WCHAR *skip_ws(const WCHAR *p)
{
    while (*p == L' ' || *p == L'\t') ++p;
    return p;
}

static BOOL starts_with_flag(const WCHAR *p, const WCHAR *flag)
{
    size_t n = (size_t)lstrlenW(flag);
    if (wcsncmp(p, flag, n) != 0) return FALSE;
    return p[n] == 0 || p[n] == L' ' || p[n] == L'\t';
}

/* 取出 argv[0] 之后的剩余部分（原样保留，不做反转义） */
static const WCHAR *cmdline_tail(const WCHAR *cmd)
{
    const WCHAR *p = skip_ws(cmd);
    if (*p == L'"') {
        ++p;
        while (*p && *p != L'"') ++p;
        if (*p) ++p;
    } else {
        while (*p && *p != L' ' && *p != L'\t') ++p;
    }
    return skip_ws(p);
}

/* ---------------------------------------------------------------- 入口 */

/* 递归删除一个目录树。用 Win32 API 而不是外部命令，避免任何 shell 依赖。 */
static void delete_tree(const WCHAR *path)
{
    WCHAR pattern[MAX_PATH * 2];
    WIN32_FIND_DATAW fd;
    HANDLE h;

    str_copy(pattern, PATH_CAP, path);
    str_append(pattern, PATH_CAP, L"\\*");
    h = FindFirstFileW(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            WCHAR child[MAX_PATH * 2];
            if (fd.cFileName[0] == L'.' &&
                (fd.cFileName[1] == 0 ||
                 (fd.cFileName[1] == L'.' && fd.cFileName[2] == 0))) {
                continue;
            }
            str_copy(child, PATH_CAP, path);
            str_append(child, PATH_CAP, L"\\");
            str_append(child, PATH_CAP, fd.cFileName);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                delete_tree(child);
            } else {
                SetFileAttributesW(child, FILE_ATTRIBUTE_NORMAL);
                DeleteFileW(child);
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryW(path);
}

/* 清掉 bin\ 下其它构建号的目录，避免每次升级都堆一份十几 MB 的旧载荷。
 * 尽力而为：旧版本程序还在运行时文件被占用，删不掉的就算了。 */
static int purge_old_versions(const WCHAR *appdir, const WCHAR *keep)
{
    WCHAR pattern[MAX_PATH * 2];
    WIN32_FIND_DATAW fd;
    HANDLE h;
    int removed = 0;

    str_copy(pattern, PATH_CAP, appdir);
    str_append(pattern, PATH_CAP, L"\\bin\\*");
    h = FindFirstFileW(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;

    do {
        WCHAR child[MAX_PATH * 2];
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        if (fd.cFileName[0] == L'.') continue;
        if (wcscmp(fd.cFileName, keep) == 0) continue;

        str_copy(child, PATH_CAP, appdir);
        str_append(child, PATH_CAP, L"\\bin\\");
        str_append(child, PATH_CAP, fd.cFileName);
        delete_tree(child);
        removed++;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return removed;
}

static void fatal(const WCHAR *text, DWORD code)
{
    WCHAR buf[LOG_CAP];
    WCHAR num[64];

    str_copy(buf, LOG_CAP, text);
    if (code != 0) {
        wsprintfW(num, L"\n\n系统错误码：%u", (unsigned int)code);
        str_append(buf, LOG_CAP, num);
    }
    MessageBoxW(NULL, buf, L"桌面图标开关", MB_OK | MB_ICONWARNING | MB_TOPMOST);
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR lpCmdLine, int nShow)
{
    WCHAR appdir[MAX_PATH * 2];
    WCHAR versioned[MAX_PATH * 2];
    WCHAR exe[MAX_PATH * 2];
    WCHAR marker[MAX_PATH * 2];
    const WCHAR *tail;
    const unsigned char *blob = NULL;
    unsigned int blob_len = 0;
    Container container;
    HANDLE mutex = NULL;
    HRSRC res;
    HGLOBAL res_data;
    int written;

    (void)hInst; (void)hPrev; (void)lpCmdLine; (void)nShow;

    /* 1) 数据目录：%LOCALAPPDATA%\HindDesktopIcons */
    if (!GetEnvironmentVariableW(L"LOCALAPPDATA", appdir, MAX_PATH)) {
        fatal(L"读不到 LOCALAPPDATA 环境变量，无法确定安装位置。", 0);
        return 2;
    }
    str_append(appdir, PATH_CAP, L"\\");
    str_append(appdir, PATH_CAP, APP_DIRNAME);

    str_copy(versioned, PATH_CAP, appdir);
    str_append(versioned, PATH_CAP, L"\\bin\\");
    str_append(versioned, PATH_CAP, L"" HDI_BUILD_ID);

    str_copy(exe, PATH_CAP, versioned);
    str_append(exe, PATH_CAP, L"\\");
    str_append(exe, PATH_CAP, PAYLOAD_EXE);

    str_copy(marker, PATH_CAP, versioned);
    str_append(marker, PATH_CAP, L"\\");
    str_append(marker, PATH_CAP, MARKER_NAME);

    tail = cmdline_tail(GetCommandLineW());

    /* 2) 原生快速路径：已有常驻实例时不再启动任何子进程，
     *    这是"再点一次就恢复"能在 30ms 内返回的关键。 */
    if (*tail == 0) {
        if (signal_event(EVENT_TOGGLE)) return 0;
    } else if (starts_with_flag(tail, L"--toggle")) {
        if (signal_event(EVENT_TOGGLE)) return 0;
    } else if (starts_with_flag(tail, L"--quit")) {
        if (signal_event(EVENT_QUIT)) return 0;
    }

    /* 3) 需要时才展开载荷（首次运行做一次，之后靠标记文件跳过） */
    if (!file_exists(marker)) {
        mutex = CreateMutexW(NULL, FALSE, MUTEX_INSTALL);
        if (mutex) WaitForSingleObject(mutex, 60000);

        if (!file_exists(marker)) {   /* 双检：可能已被另一个实例装好 */
            res = FindResourceW(NULL, MAKEINTRESOURCEW(IDR_PAYLOAD), RT_RCDATA);
            if (!res) {
                log_text(appdir, L"失败：找不到内嵌载荷资源", GetLastError());
                if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
                fatal(L"启动器内缺少内嵌载荷，文件可能已损坏或被截断。\n"
                      L"请重新获取该 exe，并检查杀毒软件是否拦截。", 0);
                return 3;
            }

            blob_len = (unsigned int)SizeofResource(NULL, res);
            res_data = LoadResource(NULL, res);
            blob = (const unsigned char *)LockResource(res_data);
            if (!blob || blob_len == 0) {
                log_text(appdir, L"失败：载荷资源无法读取", GetLastError());
                if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
                fatal(L"无法读取内嵌载荷。", 0);
                return 4;
            }

            if (!container_open(blob, blob_len, &container)) {
                log_text(appdir, L"失败：载荷容器格式不符", 0);
                if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
                fatal(L"内嵌载荷格式不正确，文件可能已损坏。", 0);
                return 5;
            }

            if (!ensure_dir(versioned)) {
                log_text(appdir, L"失败：无法创建安装目录", GetLastError());
                if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
                fatal(L"无法创建安装目录，请检查磁盘空间或安全软件限制。", 0);
                return 6;
            }

            written = install_payload(container, versioned);
            if (written < 0) {
                log_text(appdir, L"失败：解压载荷出错", GetLastError());
                if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
                fatal(L"解压程序文件失败，请检查磁盘空间或安全软件限制。", 0);
                return 7;
            }

            /* 标记文件最后写：它存在就代表整套文件都写完了 */
            if (!write_file(marker, (const unsigned char *)"ok", 2)) {
                log_text(appdir, L"警告：标记文件写入失败，下次会重新解压", GetLastError());
            }

            {
                WCHAR msg[LOG_CAP];
                str_copy(msg, LOG_CAP, L"载荷展开完成，已写出文件数：");
                wsprintfW(msg + lstrlenW(msg), L"%d", written);
                log_text(appdir, msg, 0);
            }

            /* 升级路径：清掉上一版残留的载荷目录 */
            {
                WCHAR keep[MAX_PATH];
                int purged;
                str_copy(keep, MAX_PATH, L"" HDI_BUILD_ID);
                purged = purge_old_versions(appdir, keep);
                if (purged > 0) {
                    WCHAR msg[LOG_CAP];
                    str_copy(msg, LOG_CAP, L"已清理旧版本载荷目录数：");
                    wsprintfW(msg + lstrlenW(msg), L"%d", purged);
                    log_text(appdir, msg, 0);
                }
            }
        }
        if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
    }

    /* 4) 转交参数并启动 */
    if (!launch_payload(exe, tail)) {
        log_text(appdir, L"失败：无法启动载荷程序", GetLastError());
        fatal(L"无法启动程序主体。\n请查看 launcher.log 与 app.log 了解详情。", 0);
        return 8;
    }
    return 0;
}
