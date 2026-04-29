"""
input_click — BTInputClick 鼠标点击工具。

支持 macOS、Windows、Android 三平台点击操作。
Android 通过 ADB shell input tap 实现，不依赖本地 GUI 框架。

依赖：
    pip install pyautogui pillow    # macOS / Windows
    adb（PATH 中）                   # Android
"""

import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTInputClick(BuiltinTools):
    """
    在屏幕上执行鼠标点击操作（macOS / Windows / Android）。

    坐标系统：
    - desktop：屏幕逻辑坐标（pyautogui 使用逻辑坐标，Retina 屏幕无需 ÷2）
    - android：设备像素坐标（与截图坐标一致）

    通常配合 ScreenFind 工具使用：
    1. ScreenCapture() 获取截图
    2. ScreenFind(description="登录按钮") 获取坐标 (x, y)
    3. InputClick(x=x, y=y) 执行点击
    """

    name = "InputClick"
    risk = "high"
    tags = ["input", "automation"]

    description = (
        "在屏幕指定坐标执行鼠标点击（支持 macOS / Windows / Android）。"
        "坐标来源通常是 ScreenFind 工具的返回值。"
        "desktop 模式使用屏幕逻辑坐标；android 模式使用设备像素坐标（与 ADB screencap 一致）。"
        "支持左键/右键/中键单击和双击，默认左键单击。"
    )

    params = {
        "x": ToolParam(
            type="integer",
            description="点击位置的 X 坐标（像素）。来源：ScreenFind 的返回坐标。",
        ),
        "y": ToolParam(
            type="integer",
            description="点击位置的 Y 坐标（像素）。来源：ScreenFind 的返回坐标。",
        ),
        "button": ToolParam(
            type="string",
            description="鼠标按键：'left'（左键，默认）| 'right'（右键）| 'middle'（中键）。",
            required=False,
            enum=["left", "right", "middle"],
        ),
        "clicks": ToolParam(
            type="integer",
            description="点击次数：1（单击，默认）或 2（双击）。",
            required=False,
        ),
        "target": ToolParam(
            type="string",
            description=(
                "操作目标：'desktop'（本机桌面，默认）| 'android'（Android 设备，通过 ADB）。"
            ),
            required=False,
            enum=["desktop", "android"],
        ),
        "device_id": ToolParam(
            type="string",
            description="Android 设备序列号（target='android' 且多设备时指定）。",
            required=False,
        ),
    }

    async def run(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        target: str = "desktop",
        device_id: str | None = None,
    ) -> ToolResult:
        """
        执行点击操作。

        Args:
            x:         X 坐标
            y:         Y 坐标
            button:    "left" | "right" | "middle"
            clicks:    点击次数
            target:    "desktop" | "android"
            device_id: Android 设备 ID

        Returns:
            ToolResult.ok() 操作成功，ToolResult.error() 失败。
        """
        assert x >= 0 and y >= 0, f"坐标不能为负数，得到 x={x} y={y}"
        assert clicks >= 1, f"点击次数至少 1，得到 clicks={clicks}"
        assert button in ("left", "right", "middle"), f"button 无效: {button}"

        try:
            if target == "android":
                from .._platform import input_android
                input_android.click(x, y, button=button, clicks=clicks, device_id=device_id)
            else:
                system = platform_module.system()
                if system == "Darwin":
                    from .._platform import input_macos
                    input_macos.click(x, y, button=button, clicks=clicks)
                elif system == "Windows":
                    from .._platform import input_windows
                    input_windows.click(x, y, button=button, clicks=clicks)
                else:
                    # Linux：尝试 pyautogui
                    from .._platform import input_macos
                    input_macos.click(x, y, button=button, clicks=clicks)

            logger.info("点击成功 | target={} x={} y={} button={} clicks={}", target, x, y, button, clicks)
            return ToolResult.ok(
                f"点击成功：({x}, {y})，{button} 键，{clicks} 次。"
            )

        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("点击异常 | x={} y={} error={}", x, y, e)
            return ToolResult.error(f"点击失败：{e}")
