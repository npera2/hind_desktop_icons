"""单实例控制与跨进程通知。

设计要点
--------
* 命名对象一律使用 ``Local\\`` 前缀。``Global\\`` 需要 ``SeCreateGlobalPrivilege``，
  普通用户账号下创建会失败——表现是"管理员下能用、普通用户下失效"，是最隐蔽的坑。
* 用命名事件做 IPC 而不是自建隐藏窗口类：代码量小、不依赖消息循环。
* 首次运行的实例在拿到互斥体后**立即**创建事件，缩小"另一个实例已经启动但事件还没建好"的竞态窗口；
  发送方另外做短重试兜底。
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
kernel32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

MUTEX_NAME = r"Local\HindDesktopIconsToggle"
EVENT_TOGGLE = r"Local\HindDesktopIconsToggleEvt"
EVENT_QUIT = r"Local\HindDesktopIconsQuitEvt"

_SIGNAL_RETRY = 15
_SIGNAL_RETRY_INTERVAL = 0.12


def _signal_event(name: str) -> bool:
    """打开并触发一个已存在的命名事件；不存在则短重试。"""
    for _ in range(_SIGNAL_RETRY):
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
        if handle:
            try:
                kernel32.SetEvent(wintypes.HANDLE(handle))
                return True
            finally:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
        time.sleep(_SIGNAL_RETRY_INTERVAL)
    return False


def signal_toggle() -> bool:
    """通知已常驻的实例切换一次桌面图标。"""
    return _signal_event(EVENT_TOGGLE)


def signal_quit() -> bool:
    """通知已常驻的实例退出。"""
    return _signal_event(EVENT_QUIT)


class Singleton:
    """基于命名互斥体的单实例守卫。"""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle = None
        self._mutex = None

    @property
    def is_first(self) -> bool:
        """本次是否是第一个实例（即本次创建了互斥体）。"""
        return self._handle is not None

    def acquire(self) -> tuple[bool, bool]:
        """返回 (是否拿到互斥体句柄, 是否已存在其他实例)。"""
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self._name)
        error = ctypes.get_last_error()
        if not handle:
            log.error("创建互斥体失败，Windows 错误码 %s", error)
            return False, False
        self._handle = handle
        already_exists = error == ERROR_ALREADY_EXISTS
        return True, already_exists

    def release(self) -> None:
        if self._handle:
            kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = None


class SignalChannel:
    """常驻实例侧的信号接收端。"""

    def __init__(self) -> None:
        self._handles: list[int] = []

    def create(self) -> None:
        """创建（或打开）待监听的事件句柄。"""
        self._handles = []
        for name in (EVENT_TOGGLE, EVENT_QUIT):
            handle = kernel32.CreateEventW(None, False, False, name)
            if not handle:
                raise OSError(f"创建事件 {name} 失败，错误码 {ctypes.get_last_error()}")
            self._handles.append(handle)

    def close(self) -> None:
        for handle in self._handles:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
        self._handles = []

    def wait(self, timeout_ms: int = 300) -> str | None:
        """等待任意信号，返回 "toggle" / "quit" / None（超时或出错，用于轮询退出标志）。"""
        if not self._handles:
            raise RuntimeError("SignalChannel 尚未 create()")
        array = (wintypes.HANDLE * len(self._handles))(*self._handles)
        rc = kernel32.WaitForMultipleObjects(len(self._handles), array, False, timeout_ms)
        if rc == WAIT_TIMEOUT:
            return None
        index = rc - WAIT_OBJECT_0
        if index == 0:
            return "toggle"
        if index == 1:
            return "quit"
        log.error("等待信号出错，返回码 %s，错误码 %s", rc, ctypes.get_last_error())
        return None
