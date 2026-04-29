"""
window_list — BTWindowList 窗口列表查询工具。

列出当前桌面所有可见窗口，返回 owner/title/bounds/center 等信息。
通常在调用 ScreenCapture 前先用此工具，确认正确的 app_name / bundle_id 参数值。

为什么需要这个工具：
    很多游戏/模拟器的窗口标题（kCGWindowName）为空字符串，直接用 window_title 截图会失败。
    先用 WindowList 查看所有窗口的 owner（进程名）和 title，
    再用 app_name 匹配 owner，才是稳定的截图流程。

使用示例：
    WindowList()                     → 列出本机所有可见窗口
    WindowList(target="android")     → Android 无窗口概念，返回空列表

支持平台：macOS（Quartz）、Windows（Win32 EnumWindows）
不支持平台：Android（Android 应用无桌面窗口概念）
"""

import json
import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTWindowList(BuiltinTools):
    """
    列出当前系统所有可见桌面窗口。

    返回结构化窗口列表，每条包含进程名、标题、坐标等信息。
    这是 ScreenCapture 前的排查工具：先知道窗口叫什么名字，再截图。

    典型工作流：
        1. WindowList()         → 查看所有窗口，找到目标 owner/title
        2. ScreenCapture(app_name="MuMu安卓设备")  → 精准截图
    """

    name = "WindowList"
    risk = "low"
    tags = ["screen", "read-only"]

    description = (
        "列出当前系统所有可见桌面窗口（macOS / Windows）。"
        "返回每个窗口的进程名（owner/proc）、标题、坐标、中心点等信息。"
        "使用场景：截图前先查清楚窗口名称，避免 app_name 猜错导致截全屏。"
        "注意：很多游戏窗口标题为空，owner/proc 才是稳定的匹配依据。"
        "Android 无桌面窗口概念，传 target='android' 时返回空列表。"
    )

    params = {
        "target": ToolParam(
            type="string",
            description=(
                "目标平台。"
                "'desktop'：本机桌面（macOS 或 Windows，自动检测）。"
                "'android'：Android 无桌面窗口，返回空列表。"
                "默认：'desktop'。"
            ),
            required=False,
            enum=["desktop", "android"],
        ),
    }

    async def run(self, target: str = "desktop") -> ToolResult:
        """
        列出所有可见桌面窗口。

        Args:
            target: "desktop" 或 "android"（android 返回空列表）

        Returns:
            ToolResult.ok() 包含 JSON 格式的窗口列表。
        """
        assert target in ("desktop", "android"), (
            f"target 必须是 desktop 或 android，得到: {target!r}"
        )

        if target == "android":
            logger.info("WindowList | target=android，Android 无桌面窗口概念")
            result = {"windows": [], "count": 0, "note": "Android 无桌面窗口概念"}
            return ToolResult.ok(json.dumps(result, ensure_ascii=False))

        # desktop：按操作系统分发
        system = platform_module.system()
        try:
            if system == "Darwin":
                from .._platform import window_macos
                windows = window_macos.list_windows()
            elif system == "Windows":
                from .._platform import window_windows
                windows = window_windows.list_windows()
            else:
                # Linux 暂不支持
                result = {
                    "windows": [],
                    "count": 0,
                    "note": f"当前系统 {system!r} 暂不支持 WindowList",
                }
                return ToolResult.ok(json.dumps(result, ensure_ascii=False))

            result = {"windows": windows, "count": len(windows)}
            logger.info("WindowList | system={} count={}", system, len(windows))
            return ToolResult.ok(json.dumps(result, ensure_ascii=False))

        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("WindowList 异常 | target={} error={}", target, e)
            return ToolResult.error(f"WindowList 失败：{e}")
