"""
input_type — BTInputType 文字输入工具。

在当前焦点处输入文字，支持 macOS、Windows、Android 三平台。
默认使用剪贴板粘贴（paste 模式），速度快且支持中文/Unicode。

依赖：
    pip install pyautogui pyperclip pillow    # macOS / Windows
    adb（PATH 中）                             # Android
"""

import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTInputType(BuiltinTools):
    """
    在当前焦点输入框中输入文字（支持 macOS / Windows / Android）。

    推荐先用 InputClick 点击输入框使其获得焦点，再用 InputType 输入文字。
    默认使用剪贴板粘贴（paste 模式），支持中文和 Unicode，速度远快于逐字输入。

    按键操作（如回车确认、ESC 取消、Ctrl+A 全选）请使用 key 参数。
    """

    name = "InputType"
    risk = "high"
    tags = ["input", "automation"]

    description = (
        "在当前焦点输入框中输入文字（支持 macOS / Windows / Android）。"
        "通常先用 InputClick 点击输入框，再调用此工具输入内容。"
        "默认 paste 模式（剪贴板粘贴），支持中文和特殊字符，推荐使用。"
        "也可输入按键指令（key 参数），如 'enter' 确认、'escape' 取消、'ctrl+a' 全选。"
    )

    params = {
        "text": ToolParam(
            type="string",
            description=(
                "要输入的文字内容。"
                "支持中文、英文、数字、符号等 Unicode 字符（paste 模式）。"
                "如需输入按键，请使用 key 参数而非 text。"
            ),
            required=False,
        ),
        "key": ToolParam(
            type="string",
            description=(
                "要按下的按键或快捷键。与 text 互斥，优先处理 key。"
                "单键示例：'enter', 'escape', 'tab', 'backspace', 'delete', 'space'。"
                "组合键示例（用 + 连接）：'ctrl+a', 'ctrl+c', 'command+v', 'ctrl+z'。"
                "Android 支持：'back', 'home', 'KEYCODE_ENTER' 等 ADB keyevent。"
            ),
            required=False,
        ),
        "method": ToolParam(
            type="string",
            description=(
                "输入方式（仅 text 生效时有意义）。"
                "'paste'：剪贴板粘贴（默认，快速，支持中文）。"
                "'type'：逐字符输入（慢，仅 ASCII）。"
            ),
            required=False,
            enum=["paste", "type"],
        ),
        "target": ToolParam(
            type="string",
            description="操作目标：'desktop'（默认）| 'android'。",
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
        text: str | None = None,
        key: str | None = None,
        method: str = "paste",
        target: str = "desktop",
        device_id: str | None = None,
    ) -> ToolResult:
        """
        执行文字输入或按键操作。

        Args:
            text:      要输入的文字（与 key 互斥）
            key:       要按下的按键/快捷键（与 text 互斥，优先）
            method:    "paste" | "type"
            target:    "desktop" | "android"
            device_id: Android 设备 ID

        Returns:
            ToolResult.ok() 成功，ToolResult.error() 失败。
        """
        # key 和 text 至少一个必须有值
        if key is None and (text is None or text == ""):
            return ToolResult.error("text 和 key 不能同时为空，请至少提供一个。")

        try:
            if key is not None:
                # 按键模式
                return await self._press_key(key, target, device_id)
            else:
                # 文字输入模式
                return await self._type_text(text, method, target, device_id)

        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("输入异常 | key={} text={!r} error={}", key, (text or "")[:20], e)
            return ToolResult.error(f"输入失败：{e}")

    async def _type_text(self, text: str, method: str, target: str, device_id: str | None) -> ToolResult:
        """处理文字输入（text 参数）。"""
        assert text, "text 不能为空"
        logger.info("文字输入 | target={} method={} text={!r}", target, method, text[:20])

        if target == "android":
            from .._platform import input_android
            input_android.type_text(text, method=method, device_id=device_id)
        else:
            system = platform_module.system()
            if system == "Darwin":
                from .._platform import input_macos
                input_macos.type_text(text, method=method)
            elif system == "Windows":
                from .._platform import input_windows
                input_windows.type_text(text, method=method)
            else:
                from .._platform import input_macos
                input_macos.type_text(text, method=method)

        return ToolResult.ok(f"文字输入成功：{text[:50]}{'...' if len(text) > 50 else ''}")

    async def _press_key(self, key: str, target: str, device_id: str | None) -> ToolResult:
        """处理按键操作（key 参数）。"""
        logger.info("按键操作 | target={} key={}", target, key)

        if target == "android":
            from .._platform import input_android
            input_android.press_key(key, device_id=device_id)
        else:
            system = platform_module.system()
            if system == "Darwin":
                from .._platform import input_macos
                input_macos.press_key(key)
            elif system == "Windows":
                from .._platform import input_windows
                input_windows.press_key(key)
            else:
                from .._platform import input_macos
                input_macos.press_key(key)

        return ToolResult.ok(f"按键成功：{key}")
