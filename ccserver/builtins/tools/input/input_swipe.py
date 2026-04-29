"""
input_swipe — BTInputSwipe 滑动/拖拽工具。

在屏幕上执行滑动手势，支持 macOS、Windows、Android 三平台。

常见用途：
    - 移动端：上下滑动列表、左右切换页面、手势解锁
    - 桌面端：拖拽窗口或元素到新位置

依赖：
    pip install pyautogui pillow    # macOS / Windows
    adb（PATH 中）                   # Android
"""

import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTInputSwipe(BuiltinTools):
    """
    在屏幕上执行滑动/拖拽操作（macOS / Windows / Android）。

    坐标系统：
    - desktop：屏幕逻辑坐标（pyautogui 逻辑坐标，Retina 屏无需 ÷2）
    - android：设备像素坐标（与 ADB screencap 截图坐标一致）

    常见使用场景：
    1. 向上滑动列表：x1=x2, y1=底部, y2=顶部
    2. 向右滑动返回：x1=左边缘, y1=y2=中间, x2=右侧
    3. 桌面拖拽元素：x1/y1 为起点，x2/y2 为目标位置
    """

    name = "InputSwipe"
    risk = "high"
    tags = ["input", "automation"]

    description = (
        "在屏幕上执行滑动或拖拽操作（支持 macOS / Windows / Android）。"
        "从坐标 (x1, y1) 滑动到 (x2, y2)，可指定滑动时长（毫秒）。"
        "常用于：移动端列表滑动、页面切换、桌面元素拖拽。"
        "坐标来源通常是 ScreenCapture 截图后 ScreenFind 的返回值，或 WindowInfo 的 bounds。"
    )

    params = {
        "x1": ToolParam(
            type="integer",
            description="起始点 X 坐标（像素）。",
        ),
        "y1": ToolParam(
            type="integer",
            description="起始点 Y 坐标（像素）。",
        ),
        "x2": ToolParam(
            type="integer",
            description="结束点 X 坐标（像素）。",
        ),
        "y2": ToolParam(
            type="integer",
            description="结束点 Y 坐标（像素）。",
        ),
        "duration_ms": ToolParam(
            type="integer",
            description=(
                "滑动时长，单位毫秒。"
                "值越大滑动越慢，越小越快。"
                "默认 300ms。建议范围：100ms（快速）～ 1000ms（慢速拖拽）。"
            ),
            required=False,
        ),
        "target": ToolParam(
            type="string",
            description="操作目标：'desktop'（本机桌面，默认）| 'android'（Android 设备，通过 ADB）。",
            required=False,
            enum=["desktop", "android"],
        ),
        "device_id": ToolParam(
            type="string",
            description="Android 设备序列号（target='android' 且多台设备时指定）。",
            required=False,
        ),
    }

    async def run(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
        target: str = "desktop",
        device_id: str = None,
    ) -> ToolResult:
        """
        执行滑动操作。

        Args:
            x1:          起始 X 坐标
            y1:          起始 Y 坐标
            x2:          结束 X 坐标
            y2:          结束 Y 坐标
            duration_ms: 滑动时长（毫秒），默认 300
            target:      "desktop" 或 "android"
            device_id:   Android 设备 ID（多设备时使用）

        Returns:
            ToolResult.ok() 成功，ToolResult.error() 失败。
        """
        # 参数合法性检查
        assert x1 >= 0 and y1 >= 0, f"起始坐标不能为负数，得到 x1={x1} y1={y1}"
        assert x2 >= 0 and y2 >= 0, f"结束坐标不能为负数，得到 x2={x2} y2={y2}"
        assert duration_ms > 0, f"duration_ms 必须大于 0，得到 {duration_ms}"
        assert target in ("desktop", "android"), f"target 必须是 desktop 或 android，得到 {target}"

        # 滑动时长：毫秒 → 秒（pyautogui 使用秒）
        duration_sec = duration_ms / 1000.0

        try:
            if target == "android":
                from .._platform import input_android
                input_android.drag_to(
                    x=x2, y=y2,
                    duration=duration_sec,
                    start_x=x1, start_y=y1,
                    device_id=device_id,
                )
            else:
                # 桌面端：先移动到起点，再拖拽到终点
                system = platform_module.system()
                if system == "Darwin":
                    from .._platform import input_macos
                    input_macos.move_to(x1, y1, duration=0.1)
                    input_macos.drag_to(x2, y2, duration=duration_sec)
                elif system == "Windows":
                    from .._platform import input_windows
                    input_windows.move_to(x1, y1, duration=0.1)
                    input_windows.drag_to(x2, y2, duration=duration_sec)
                else:
                    # Linux：尝试 pyautogui（X11 环境）
                    from .._platform import input_macos
                    input_macos.move_to(x1, y1, duration=0.1)
                    input_macos.drag_to(x2, y2, duration=duration_sec)

            logger.info(
                "滑动成功 | target={} ({},{}) -> ({},{}) {}ms",
                target, x1, y1, x2, y2, duration_ms
            )
            return ToolResult.ok(
                f"滑动成功：({x1}, {y1}) → ({x2}, {y2})，时长 {duration_ms}ms。"
            )

        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("滑动异常 | ({},{}) -> ({},{}) error={}", x1, y1, x2, y2, e)
            return ToolResult.error(f"滑动失败：{e}")
