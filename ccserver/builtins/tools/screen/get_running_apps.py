"""
get_running_apps — BTGetRunningApps 运行中进程查询工具。

查询当前系统所有运行中的进程（包括无窗口的后台进程），
补充 WindowList 只能看到"有窗口进程"的盲区。

典型用途：
    1. 判断某应用是否在运行（不需要截图）
    2. 获取 bundle_id / pid（用于后续 WindowControl 精确定位）
    3. 查看哪些进程在后台运行

支持平台：macOS（NSWorkspace）、Windows（psutil）、Android（adb shell ps + dumpsys）
"""

import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


class BTGetRunningApps(BuiltinTools):
    """
    查询当前系统所有运行中的进程。

    与 WindowList 的区别：
        WindowList  — 只返回有可见窗口的应用，侧重窗口坐标信息。
        GetRunningApps — 返回所有进程（含后台），侧重"某应用是否在运行"。

    返回自然语言列表，含进程名、PID、Bundle ID（macOS）、是否有窗口。
    """

    name = "GetRunningApps"
    risk = "low"
    tags = ["screen", "read-only"]

    description = (
        "查询当前系统所有运行中的进程（包括无窗口的后台进程）。"
        "与 WindowList 的区别：WindowList 只返回有可见窗口的应用；"
        "GetRunningApps 返回全部进程，用于判断某应用是否在运行、获取 bundle_id/pid。"
        "支持按名称过滤，支持只返回有窗口的进程。"
        "macOS 返回 Bundle ID，Windows 返回进程名（.exe），Android 返回包名列表。"
    )

    params = {
        "filter": ToolParam(
            type="string",
            description=(
                "按名称过滤（大小写不敏感，部分匹配）。"
                "macOS/Windows 匹配进程名；Android 匹配包名。"
                "省略时返回全部进程。"
                "示例：'Chrome' 会匹配 'Google Chrome'、'chromedriver' 等。"
            ),
            required=False,
        ),
        "with_windows_only": ToolParam(
            type="boolean",
            description=(
                "只返回有可见窗口的进程，默认 false（返回全部）。"
                "true 时等价于 WindowList 的进程视角，但不含坐标信息。"
            ),
            required=False,
        ),
        "target": ToolParam(
            type="string",
            description=(
                "'desktop'：本机桌面（macOS 或 Windows，自动检测，默认）。"
                "'android'：Android 设备，返回已安装应用包名列表（通过 ADB pm list packages）。"
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
        filter: str | None = None,
        with_windows_only: bool = False,
        target: str = "desktop",
        device_id: str | None = None,
    ) -> ToolResult:
        """
        查询运行中的进程列表。

        Args:
            filter:            按名称过滤，None 返回全部
            with_windows_only: 只返回有窗口的进程
            target:            "desktop" 或 "android"
            device_id:         Android 设备 ID

        Returns:
            ToolResult.ok() 自然语言列表。
        """
        assert target in ("desktop", "android"), f"target 必须是 desktop 或 android，得到: {target!r}"

        try:
            if target == "android":
                return self._run_android(filter, device_id)

            system = platform_module.system()
            if system == "Darwin":
                from .._platform import apps_macos
                apps = apps_macos.get_running_apps(
                    filter_name=filter,
                    with_windows_only=with_windows_only,
                )
            elif system == "Windows":
                from .._platform import apps_windows
                apps = apps_windows.get_running_apps(
                    filter_name=filter,
                    with_windows_only=with_windows_only,
                )
            else:
                return ToolResult.error(f"当前系统 {system!r} 暂不支持 GetRunningApps。")

            return self._format_apps(apps, filter, with_windows_only, system)

        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("GetRunningApps 异常 | target={} error={}", target, e)
            return ToolResult.error(f"查询运行中进程失败：{e}")

    def _format_apps(
        self,
        apps: list[dict],
        filter_name: str | None,
        with_windows_only: bool,
        system: str,
    ) -> ToolResult:
        """将进程列表格式化为自然语言输出。"""
        if not apps:
            suffix = f"（过滤条件：{filter_name!r}）" if filter_name else ""
            return ToolResult.ok(f"没有找到匹配的运行中进程{suffix}。")

        platform_name = {"Darwin": "macOS", "Windows": "Windows"}.get(system, system)
        filter_note = f"，名称含「{filter_name}」" if filter_name else ""
        window_note = "，仅显示有窗口的进程" if with_windows_only else ""
        lines = [f"共检测到 {len(apps)} 个运行中的进程（平台：{platform_name}{filter_note}{window_note}）：\n"]

        for i, app in enumerate(apps, 1):
            name = app.get("name", "（未知）")
            pid = app.get("pid", "?")
            bundle_id = app.get("bundle_id", "")
            has_window = app.get("has_window", False)

            parts = [f"[{i}] 进程：{name}，PID：{pid}"]
            if bundle_id:
                parts.append(f"Bundle ID：{bundle_id}")
            parts.append(f"有窗口：{'是' if has_window else '否'}")
            lines.append("，".join(parts))

        logger.info("GetRunningApps 格式化完成 | count={}", len(apps))
        return ToolResult.ok("\n".join(lines))

    def _run_android(self, filter_name: str | None, device_id: str | None) -> ToolResult:
        """Android：通过 ADB 获取已安装应用包名列表（复用 AndroidCtrl 逻辑）。"""
        from .._platform.capture_android import _run_adb
        output = _run_adb(
            ["shell", "pm", "list", "packages"],
            device_id=device_id,
        ).decode("utf-8", errors="replace")

        packages = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkg = line[len("package:"):]
                pkg = pkg.strip()
                if filter_name is None or filter_name.lower() in pkg.lower():
                    packages.append(pkg)

        if not packages:
            return ToolResult.ok("没有找到匹配的 Android 应用包。")

        filter_note = f"，名称含「{filter_name}」" if filter_name else ""
        lines = [f"共检测到 {len(packages)} 个 Android 应用包{filter_note}：\n"]
        for i, pkg in enumerate(packages, 1):
            lines.append(f"[{i}] {pkg}")
        return ToolResult.ok("\n".join(lines))
