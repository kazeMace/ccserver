"""
window_control — BTWindowControl 窗口管理操作工具。

对指定窗口执行管理操作：激活到前台、移动、调整大小、最小化、最大化、关闭。

所有操作通过 action 参数路由，窗口定位方式与 WindowInfo/ScreenCapture 一致：
    bundle_id（最精确，仅 macOS）> app_name（最稳定）> window_title（标题易变）

支持平台：macOS（AppleScript + AppKit）、Windows（win32gui）
不支持：Android（Android 应用无桌面窗口管理概念）
"""

import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTWindowControl(BuiltinTools):
    """
    对指定桌面窗口执行管理操作。

    支持的操作（action 参数）：
        focus        — 激活窗口到前台
        move         — 移动窗口到指定桌面坐标（保持大小不变）
        resize       — 调整窗口大小（保持位置不变）
        move_resize  — 同时移动+调整大小（一次系统调用，更高效）
        minimize     — 最小化窗口
        maximize     — 最大化窗口
        close        — 关闭窗口（不可逆，谨慎使用）

    窗口定位优先级：bundle_id > app_name > window_title
    """

    name = "WindowControl"
    risk = "high"
    tags = ["screen", "automation"]

    description = (
        "对桌面窗口执行管理操作：激活到前台、移动位置、调整大小、最小化、最大化、关闭。"
        "通过 action 参数选择操作类型，通过 app_name/bundle_id/window_title 定位目标窗口。"
        "move/resize/move_resize 操作前会查询当前窗口位置，返回值包含变更前后的坐标对比。"
        "close 操作不可逆，执行前请确认。macOS 和 Windows 均支持，Android 不支持。"
    )

    params = {
        "action": ToolParam(
            type="string",
            description=(
                "操作类型：\n"
                "- focus:       将窗口激活到前台（取消最小化并置顶）\n"
                "- move:        移动窗口左上角到桌面坐标 (x, y)，需提供 x/y 参数\n"
                "- resize:      调整窗口宽高，需提供 width/height 参数\n"
                "- move_resize: 同时移动+调整大小，需提供 x/y/width/height 参数\n"
                "- minimize:    最小化窗口到任务栏/Dock\n"
                "- maximize:    最大化/全屏窗口\n"
                "- close:       关闭窗口（不可逆）"
            ),
            enum=["focus", "move", "resize", "move_resize", "minimize", "maximize", "close"],
        ),
        "app_name": ToolParam(
            type="string",
            description=(
                "按进程/应用名定位窗口（大小写不敏感，部分匹配）。"
                "macOS 匹配 kCGWindowOwnerName，Windows 匹配 .exe 进程名。"
                "示例：'Google Chrome'、'WeChat'、'MuMu安卓设备'。"
            ),
            required=False,
        ),
        "window_title": ToolParam(
            type="string",
            description=(
                "按窗口标题定位（大小写不敏感，部分匹配）。"
                "注意：很多应用窗口标题会动态变化，优先使用 app_name 或 bundle_id。"
            ),
            required=False,
        ),
        "bundle_id": ToolParam(
            type="string",
            description=(
                "按 macOS Bundle ID 精确定位（仅 macOS）。"
                "最精确的定位方式，不受应用名/标题变化影响。"
                "示例：'com.google.Chrome'、'com.tencent.wechat'。"
            ),
            required=False,
        ),
        "x": ToolParam(
            type="integer",
            description="目标 X 坐标（桌面像素）。action 为 move 或 move_resize 时必填。",
            required=False,
        ),
        "y": ToolParam(
            type="integer",
            description="目标 Y 坐标（桌面像素）。action 为 move 或 move_resize 时必填。",
            required=False,
        ),
        "width": ToolParam(
            type="integer",
            description="目标宽度（像素）。action 为 resize 或 move_resize 时必填。",
            required=False,
        ),
        "height": ToolParam(
            type="integer",
            description="目标高度（像素）。action 为 resize 或 move_resize 时必填。",
            required=False,
        ),
    }

    async def run(
        self,
        action: str,
        app_name: str | None = None,
        window_title: str | None = None,
        bundle_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> ToolResult:
        """
        执行窗口管理操作。

        Args:
            action:       操作类型
            app_name:     按进程名定位
            window_title: 按窗口标题定位
            bundle_id:    按 Bundle ID 定位（macOS 最精确）
            x/y:          move/move_resize 的目标坐标
            width/height: resize/move_resize 的目标尺寸

        Returns:
            ToolResult.ok() 操作成功，ToolResult.error() 失败。
        """
        assert action in ("focus", "move", "resize", "move_resize", "minimize", "maximize", "close"), \
            f"action 无效：{action!r}"

        # 至少需要一个窗口定位参数
        if not app_name and not window_title and not bundle_id:
            return ToolResult.error(
                "需要至少提供 app_name、window_title、bundle_id 中的一个来定位窗口。"
            )

        # 参数完整性检查
        if action == "move" and (x is None or y is None):
            return ToolResult.error("action='move' 需要提供 x 和 y 参数。")
        if action == "resize" and (width is None or height is None):
            return ToolResult.error("action='resize' 需要提供 width 和 height 参数。")
        if action == "move_resize" and (x is None or y is None or width is None or height is None):
            return ToolResult.error("action='move_resize' 需要提供 x、y、width、height 参数。")

        system = platform_module.system()
        if system not in ("Darwin", "Windows"):
            return ToolResult.error(f"当前系统 {system!r} 暂不支持 WindowControl。")

        try:
            result = self._dispatch(
                system, action, app_name, window_title, bundle_id, x, y, width, height
            )
        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("WindowControl 异常 | action={} error={}", action, e)
            return ToolResult.error(f"操作失败：{e}")

        if "error" in result:
            hint = "请先用 WindowList 确认窗口进程名。" if "未找到匹配窗口" in result["error"] else ""
            return ToolResult.error(f"{result['error']}{' ' + hint if hint else ''}")

        return ToolResult.ok(self._format_ok(action, result))

    def _dispatch(
        self,
        system: str,
        action: str,
        app_name: str | None,
        window_title: str | None,
        bundle_id: str | None,
        x: int | None,
        y: int | None,
        width: int | None,
        height: int | None,
    ) -> dict:
        """按操作系统和 action 分发到平台层函数。"""
        if system == "Darwin":
            from .._platform import apps_macos as plat
        else:
            from .._platform import apps_windows as plat

        kwargs = dict(app_name=app_name, window_title=window_title, bundle_id=bundle_id)

        if action == "focus":
            return plat.focus_window(**kwargs)
        elif action == "move":
            return plat.move_window(x=x, y=y, **kwargs)
        elif action == "resize":
            return plat.resize_window(width=width, height=height, **kwargs)
        elif action == "move_resize":
            return plat.move_resize_window(x=x, y=y, width=width, height=height, **kwargs)
        elif action == "minimize":
            return plat.minimize_window(**kwargs)
        elif action == "maximize":
            return plat.maximize_window(**kwargs)
        elif action == "close":
            return plat.close_window(**kwargs)
        else:
            return {"error": f"未知 action：{action!r}"}

    def _format_ok(self, action: str, result: dict) -> str:
        """将平台层返回的成功结果格式化为自然语言。"""
        owner = result.get("owner", "?")
        title = result.get("title") or "（无标题）"
        base = f"进程「{owner}」窗口「{title}」"

        if action == "focus":
            return f"已将{base}激活到前台。"

        elif action == "move":
            frm = result.get("from", ["?", "?"])
            to = result.get("to", ["?", "?"])
            return f"已将{base}从 ({frm[0]}, {frm[1]}) 移动到 ({to[0]}, {to[1]})。"

        elif action == "resize":
            frm = result.get("from", ["?", "?"])
            to = result.get("to", ["?", "?"])
            return f"已将{base}从 {frm[0]}x{frm[1]} 调整为 {to[0]}x{to[1]}。"

        elif action == "move_resize":
            frm = result.get("from", {})
            to = result.get("to", {})
            return (
                f"已将{base}移动并调整大小。"
                f"变更前：位置 ({frm.get('x','?')}, {frm.get('y','?')}) 大小 {frm.get('width','?')}x{frm.get('height','?')}。"
                f"变更后：位置 ({to.get('x','?')}, {to.get('y','?')}) 大小 {to.get('width','?')}x{to.get('height','?')}。"
            )

        elif action == "minimize":
            return f"已最小化{base}。"

        elif action == "maximize":
            return f"已最大化{base}。"

        elif action == "close":
            return f"已关闭{base}。"

        return f"操作 {action} 成功。"
