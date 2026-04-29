"""
window_info — BTWindowInfo 窗口几何信息查询工具（不截图）。

查询指定窗口在桌面上的精确坐标（bounds/center），
以及前台状态、所在显示器、跨屏情况等信息。

为什么需要这个工具：
    Agent 点击窗口内部的某个元素时，需要知道：
    1. 窗口的桌面坐标（bounds.x, bounds.y）
    2. 窗口内部的相对坐标 → 转换为桌面绝对坐标
    例：Android 游戏坐标 (540, 960) → 桌面坐标 = window.bounds + 比例换算

    截图时如果同时需要窗口坐标，也可以直接读 ScreenCapture 返回的 window_geometry 字段。
    本工具适用于"只需要坐标信息，不需要截图"的场景（响应更快）。

使用示例：
    WindowInfo(app_name="MuMu安卓设备")
    WindowInfo(bundle_id="com.netease.mumu.nemux.emulator")
    WindowInfo(window_title="My Browser")

匹配优先级：bundle_id（最精确）> app_name（最稳定）> window_title（可能为空）
"""

import json
import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTWindowInfo(BuiltinTools):
    """
    查询单个窗口的详细几何信息（不截图，响应快）。

    返回窗口的桌面坐标、四角坐标、中心点、前台状态、所在显示器等。
    用于坐标计算：已知窗口内部相对坐标时，换算为桌面绝对坐标。
    """

    name = "WindowInfo"
    risk = "low"
    tags = ["screen", "read-only"]

    description = (
        "查询指定窗口的几何信息（不截图）。"
        "返回：bounds（x/y/width/height）、center、四角坐标、is_foreground、所在显示器。"
        "典型用途：截图后需要点击窗口内某坐标时，用此工具获取窗口在桌面上的位置，再做坐标换算。"
        "匹配方式：bundle_id（最精确）> app_name（最稳定）> window_title（游戏标题常为空）。"
    )

    params = {
        "app_name": ToolParam(
            type="string",
            description=(
                "按进程/应用名匹配（macOS: kCGWindowOwnerName，Windows: 进程名）。"
                "大小写不敏感，支持部分匹配。"
                "示例：'MuMu安卓设备'、'Google Chrome'、'WeChat'。"
            ),
            required=False,
        ),
        "window_title": ToolParam(
            type="string",
            description=(
                "按窗口标题匹配（大小写不敏感，部分匹配）。"
                "注意：很多游戏/模拟器窗口标题为空，此时应改用 app_name。"
            ),
            required=False,
        ),
        "bundle_id": ToolParam(
            type="string",
            description=(
                "按 macOS Bundle ID 精确匹配（仅 macOS）。"
                "示例：'com.netease.mumu.nemux.emulator'、'com.google.Chrome'。"
                "是最精确的定位方式，不受应用名/标题变化影响。"
            ),
            required=False,
        ),
    }

    async def run(
        self,
        app_name: str = None,
        window_title: str = None,
        bundle_id: str = None,
    ) -> ToolResult:
        """
        查询窗口几何信息。

        Args:
            app_name:     按进程/应用名匹配
            window_title: 按窗口标题匹配
            bundle_id:    按 Bundle ID 匹配（仅 macOS）

        Returns:
            ToolResult.ok() 包含详细的窗口几何 JSON。
        """
        # 至少要有一个匹配条件
        if not app_name and not window_title and not bundle_id:
            return ToolResult.error(
                "需要至少提供 app_name、window_title、bundle_id 中的一个"
            )

        system = platform_module.system()
        try:
            if system == "Darwin":
                from .._platform import window_macos
                info = window_macos.get_window_info(
                    app_name=app_name,
                    window_title=window_title,
                    bundle_id=bundle_id,
                )
            elif system == "Windows":
                from .._platform import window_windows
                info = window_windows.get_window_info(
                    app_name=app_name,
                    window_title=window_title,
                    bundle_id=bundle_id,
                )
            else:
                return ToolResult.error(
                    f"当前系统 {system!r} 暂不支持 WindowInfo"
                )

            # 检查是否返回了 error 字段
            if "error" in info:
                return ToolResult.error(info["error"])

            logger.info(
                "WindowInfo 成功 | app={} title={} bundle={}",
                app_name, window_title, bundle_id
            )
            return ToolResult.ok(json.dumps(info, ensure_ascii=False))

        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception(
                "WindowInfo 异常 | app={} title={} bundle={} error={}",
                app_name, window_title, bundle_id, e
            )
            return ToolResult.error(f"WindowInfo 失败：{e}")
