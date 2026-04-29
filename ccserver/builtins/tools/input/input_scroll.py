"""
input_scroll — BTInputScroll 滚动工具。

在屏幕指定位置滚动鼠标滚轮或模拟滑动，支持 macOS、Windows、Android 三平台。

常见用途：
    - 桌面端：滚动网页、文档、下拉列表
    - 移动端：上下滚动内容区域（通过 swipe 模拟）

依赖：
    pip install pyautogui pillow    # macOS / Windows
    adb（PATH 中）                   # Android
"""

import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTInputScroll(BuiltinTools):
    """
    在屏幕指定坐标执行滚动操作（macOS / Windows / Android）。

    桌面端使用鼠标滚轮，Android 端通过 swipe 模拟滚动。

    delta 说明：
    - 正数：向上滚动（内容向下移动，即"往上看"）
    - 负数：向下滚动（内容向上移动，即"往下看"）
    - 建议每次滚动 3～5 单位，每单位约 100px
    """

    name = "InputScroll"
    risk = "high"
    tags = ["input", "automation"]

    description = (
        "在屏幕指定坐标执行滚动操作（支持 macOS / Windows / Android）。"
        "桌面端滚动鼠标滚轮；Android 端通过 swipe 模拟。"
        "delta 正数向上滚动，负数向下滚动。"
        "常用于：滚动网页查看内容、下拉选择框、长列表导航。"
    )

    params = {
        "x": ToolParam(
            type="integer",
            description="滚动位置的 X 坐标（像素）。鼠标将移动到此位置后执行滚动。",
        ),
        "y": ToolParam(
            type="integer",
            description="滚动位置的 Y 坐标（像素）。",
        ),
        "delta": ToolParam(
            type="integer",
            description=(
                "滚动量。正数向上（内容下移），负数向下（内容上移）。"
                "建议范围：±1 到 ±10，每单位约 100px。"
                "例如 delta=3 向上滚动约 300px，delta=-5 向下滚动约 500px。"
            ),
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
        x: int,
        y: int,
        delta: int,
        target: str = "desktop",
        device_id: str = None,
    ) -> ToolResult:
        """
        执行滚动操作。

        Args:
            x:         滚动位置 X 坐标
            y:         滚动位置 Y 坐标
            delta:     滚动量（正数向上，负数向下）
            target:    "desktop" 或 "android"
            device_id: Android 设备 ID（多设备时使用）

        Returns:
            ToolResult.ok() 成功，ToolResult.error() 失败。
        """
        assert x >= 0 and y >= 0, f"坐标不能为负数，得到 x={x} y={y}"
        assert delta != 0, "delta 不能为 0，请指定滚动方向和距离"
        assert target in ("desktop", "android"), f"target 必须是 desktop 或 android，得到 {target}"

        try:
            if target == "android":
                from .._platform import input_android
                input_android.scroll(x, y, delta, device_id=device_id)
            else:
                system = platform_module.system()
                if system == "Darwin":
                    from .._platform import input_macos
                    input_macos.scroll(x, y, delta)
                elif system == "Windows":
                    from .._platform import input_windows
                    input_windows.scroll(x, y, delta)
                else:
                    # Linux：尝试 pyautogui（X11 环境）
                    from .._platform import input_macos
                    input_macos.scroll(x, y, delta)

            direction = "向上" if delta > 0 else "向下"
            logger.info(
                "滚动成功 | target={} x={} y={} delta={} {}",
                target, x, y, delta, direction
            )
            return ToolResult.ok(
                f"滚动成功：位置 ({x}, {y})，{direction} {abs(delta)} 单位。"
            )

        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("滚动异常 | x={} y={} delta={} error={}", x, y, delta, e)
            return ToolResult.error(f"滚动失败：{e}")
