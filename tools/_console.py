"""让 tools/ 下的脚本在任意系统代码页下都能安全打印中文。

背景：Windows 上控制台重定向（管道、CI 日志、写文件）时，Python 的 stdout 编码取自
系统区域设置。中文版 Windows 是 cp936，能编码中文；而英文版 Windows 是 cp1252，
`print("已生成 ...")` 会直接抛 UnicodeEncodeError 并以退出码 1 结束。

这里只把编码**错误策略**放宽为 replace（编不出来的字符退化成 `?`），不改编码本身，
所以在中文控制台上中文依然正常显示。CI 上则由工作流设 PYTHONUTF8=1 走 UTF-8 输出。
"""

from __future__ import annotations

import sys


def setup_console() -> None:
    """放宽 stdout / stderr 的编码错误策略，避免打印中文时崩溃。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            # 流不支持重配置（例如被替换成了自定义对象），忽略即可
            pass
