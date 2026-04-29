"""
_platform — 平台截图与输入控制适配层。

根据运行时 platform.system() 自动选择对应实现：
  - macOS   → capture_macos.py  / input_macos.py
  - Windows → capture_windows.py / input_windows.py
  - Android → capture_android.py / input_android.py（通过 ADB，不依赖本地平台）

所有适配模块导出统一接口：
  capture(region=None) -> bytes          截图，返回 PNG bytes
  capture_window(title) -> bytes         截取指定标题窗口，返回 PNG bytes
  click(x, y, button, clicks) -> None    鼠标点击
  type_text(text, method) -> None        文字输入

Usage:
    from ccserver.builtins.tools._platform import get_platform_name
    name = get_platform_name()
"""

import platform


def get_platform_name() -> str:
    """
    返回当前运行平台名称。

    Returns:
        "macos" | "windows" | "linux"
    """
    system = platform.system()
    if system == "Darwin":
        return "macos"
    elif system == "Windows":
        return "windows"
    else:
        return "linux"
