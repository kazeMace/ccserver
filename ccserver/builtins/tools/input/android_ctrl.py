"""
android_ctrl — BTAndroidCtrl Android 设备控制工具。

通过 ADB 命令控制 Android 设备，提供常用操作的结构化接口。
避免 Agent 手动拼接 adb shell 命令出错，也是视觉不可用时的降级操作路径。

支持的 action：
    list_devices    — 列出已连接设备
    list_packages   — 列出已安装应用（包名 + 应用名）
    start_app       — 启动应用
    stop_app        — 停止应用
    get_focus       — 获取当前前台 Activity（判断哪个应用在运行）
    tap             — 模拟点击（坐标）
    swipe           — 模拟滑动（起点→终点）
    keyevent        — 发送按键（back/home/enter 等）
    shell           — 执行任意 adb shell 命令（高级用途）

依赖：
    adb 命令行工具（需在 PATH 中）
    Android 设备开启 USB 调试模式

使用示例：
    AndroidCtrl(action="list_devices")
    AndroidCtrl(action="list_packages", device_id="emulator-5554")
    AndroidCtrl(action="start_app", package="com.tencent.mm")
    AndroidCtrl(action="tap", x=540, y=960)
    AndroidCtrl(action="swipe", x1=540, y1=1200, x2=540, y2=400, duration_ms=300)
    AndroidCtrl(action="keyevent", key="back")
    AndroidCtrl(action="get_focus")
"""

from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult
from ccserver.builtins.tools._platform.capture_android import _run_adb, list_devices


# ADB shell 命令输出最大长度（避免超长输出淹没 token）
_MAX_OUTPUT_LEN = 20_000


class BTAndroidCtrl(BuiltinTools):
    """
    通过 ADB 控制 Android 设备。

    提供常用 Android 操作的结构化接口，无需手动拼接 adb 命令。
    视觉识别（ScreenCapture）不可用时，可通过此工具完成应用管理和交互操作。

    所有操作均通过 action 参数路由，不同 action 需要不同的附加参数。
    """

    name = "AndroidCtrl"
    risk = "high"
    tags = ["input", "automation"]

    description = (
        "通过 ADB 控制 Android 设备，提供应用管理、触控输入、按键操作等功能。"
        "适用场景：查询设备/应用信息、启动/停止应用、模拟点击滑动、发送按键。"
        "也是视觉识别不可用时的降级操作路径（无需截图即可操作 Android）。"
        "需要 adb 在系统 PATH 中，且设备已开启 USB 调试模式。"
    )

    params = {
        "action": ToolParam(
            type="string",
            description=(
                "要执行的操作类型：\n"
                "- list_devices：列出所有已连接 Android 设备的序列号\n"
                "- list_packages：列出设备上已安装的应用（返回包名列表）\n"
                "- start_app：启动指定包名的应用（需要 package 参数）\n"
                "- stop_app：强制停止指定包名的应用（需要 package 参数）\n"
                "- get_focus：获取当前前台 Activity（判断哪个应用在运行）\n"
                "- tap：模拟屏幕点击（需要 x、y 参数）\n"
                "- swipe：模拟屏幕滑动（需要 x1、y1、x2、y2 参数）\n"
                "- keyevent：发送按键事件（需要 key 参数，如 back/home/enter）\n"
                "- shell：执行任意 adb shell 命令（需要 command 参数，高级用途）\n"
                "- ui_dump：获取当前界面的 UI 节点树（uiautomator dump），"
                "返回可交互元素列表（text/bounds/class/clickable），视觉不可用时的结构化替代方案"
            ),
            enum=[
                "list_devices", "list_packages", "start_app", "stop_app",
                "get_focus", "tap", "swipe", "keyevent", "shell", "ui_dump",
            ],
        ),
        "device_id": ToolParam(
            type="string",
            description="Android 设备序列号（多台设备时必须指定，单台可省略）。",
            required=False,
        ),
        "package": ToolParam(
            type="string",
            description="应用包名（start_app / stop_app 时必须提供）。例如：'com.tencent.mm'。",
            required=False,
        ),
        "x": ToolParam(
            type="integer",
            description="点击位置 X 坐标（tap 时必须提供，单位：设备像素）。",
            required=False,
        ),
        "y": ToolParam(
            type="integer",
            description="点击位置 Y 坐标（tap 时必须提供，单位：设备像素）。",
            required=False,
        ),
        "x1": ToolParam(
            type="integer",
            description="滑动起始 X 坐标（swipe 时必须提供）。",
            required=False,
        ),
        "y1": ToolParam(
            type="integer",
            description="滑动起始 Y 坐标（swipe 时必须提供）。",
            required=False,
        ),
        "x2": ToolParam(
            type="integer",
            description="滑动结束 X 坐标（swipe 时必须提供）。",
            required=False,
        ),
        "y2": ToolParam(
            type="integer",
            description="滑动结束 Y 坐标（swipe 时必须提供）。",
            required=False,
        ),
        "duration_ms": ToolParam(
            type="integer",
            description="滑动时长（swipe 时可选，单位毫秒，默认 300ms）。",
            required=False,
        ),
        "key": ToolParam(
            type="string",
            description=(
                "按键名称（keyevent 时必须提供）。"
                "常用值：'back'（返回）、'home'（主页）、'enter'（确认）、"
                "'escape'（退出）、'delete'（删除）、'tab'（制表符）。"
                "也支持完整 keycode：'KEYCODE_BACK'、'KEYCODE_VOLUME_UP' 等。"
            ),
            required=False,
        ),
        "command": ToolParam(
            type="string",
            description=(
                "adb shell 命令（shell action 时必须提供）。"
                "例如：'pm list packages'、'dumpsys window | grep mCurrentFocus'。"
                "注意：此参数直接传给 adb shell 执行，请确认命令安全性。"
            ),
            required=False,
        ),
    }

    async def run(
        self,
        action: str,
        device_id: str = None,
        package: str = None,
        x: int = None,
        y: int = None,
        x1: int = None,
        y1: int = None,
        x2: int = None,
        y2: int = None,
        duration_ms: int = 300,
        key: str = None,
        command: str = None,
    ) -> ToolResult:
        """
        执行 Android 设备控制操作。

        根据 action 参数路由到对应的处理函数。

        Args:
            action:      操作类型（见 params 说明）
            device_id:   设备序列号（多设备时必填）
            package:     应用包名（start_app/stop_app）
            x, y:        点击坐标（tap）
            x1,y1,x2,y2: 滑动坐标（swipe）
            duration_ms: 滑动时长（swipe，默认 300ms）
            key:         按键名（keyevent）
            command:     shell 命令（shell）

        Returns:
            ToolResult.ok() 含操作结果字符串或 JSON，ToolResult.error() 失败。
        """
        assert action, "action 不能为空"

        try:
            if action == "list_devices":
                return self._list_devices()

            elif action == "list_packages":
                return self._list_packages(device_id)

            elif action == "start_app":
                assert package, "start_app 需要 package 参数"
                return self._start_app(package, device_id)

            elif action == "stop_app":
                assert package, "stop_app 需要 package 参数"
                return self._stop_app(package, device_id)

            elif action == "get_focus":
                return self._get_focus(device_id)

            elif action == "tap":
                assert x is not None and y is not None, "tap 需要 x 和 y 参数"
                return self._tap(x, y, device_id)

            elif action == "swipe":
                assert all(v is not None for v in [x1, y1, x2, y2]), \
                    "swipe 需要 x1、y1、x2、y2 参数"
                return self._swipe(x1, y1, x2, y2, duration_ms, device_id)

            elif action == "keyevent":
                assert key, "keyevent 需要 key 参数"
                return self._keyevent(key, device_id)

            elif action == "shell":
                assert command, "shell 需要 command 参数"
                return self._shell(command, device_id)

            elif action == "ui_dump":
                return self._ui_dump(device_id)

            else:
                return ToolResult.error(f"未知 action：{action!r}")

        except AssertionError as e:
            return ToolResult.error(str(e))
        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("AndroidCtrl 异常 | action={} error={}", action, e)
            return ToolResult.error(f"操作失败：{e}")

    # ── 各 action 实现 ─────────────────────────────────────────────────────────

    def _list_devices(self) -> ToolResult:
        """列出所有已连接的 Android 设备。"""
        devices = list_devices()
        if not devices:
            return ToolResult.ok("没有已连接的 Android 设备。请确认 USB 调试已开启并设备已连接。")
        logger.info("AndroidCtrl list_devices | count={}", len(devices))
        lines = [f"共检测到 {len(devices)} 台已连接的 Android 设备："]
        for i, d in enumerate(devices, 1):
            lines.append(f"[{i}] 设备 ID：{d}")
        return ToolResult.ok("\n".join(lines))

    def _list_packages(self, device_id: str = None) -> ToolResult:
        """列出设备上已安装的应用包名。"""
        output = _run_adb(
            ["shell", "pm", "list", "packages"],
            device_id=device_id,
        ).decode("utf-8", errors="replace")

        # 每行格式：package:com.example.app
        packages = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkg = line[len("package:"):]
                packages.append(pkg.strip())

        logger.info("AndroidCtrl list_packages | count={} device={}", len(packages), device_id or "default")
        if not packages:
            return ToolResult.ok("设备上没有检测到已安装的应用包。")
        lines = [f"共检测到 {len(packages)} 个已安装的应用包："]
        for i, pkg in enumerate(packages, 1):
            lines.append(f"[{i}] {pkg}")
        return ToolResult.ok("\n".join(lines))

    def _start_app(self, package: str, device_id: str = None) -> ToolResult:
        """
        启动指定包名的应用。

        先查询该应用的主 Activity，再通过 am start 启动。
        如果查不到主 Activity，尝试用 monkey 方式启动。
        """
        # 先查询主 Activity
        activity = self._find_main_activity(package, device_id)

        if activity:
            # 用 am start -n 启动指定 Activity
            _run_adb(
                ["shell", "am", "start", "-n", f"{package}/{activity}"],
                device_id=device_id,
            )
            logger.info("AndroidCtrl start_app | package={} activity={}", package, activity)
            return ToolResult.ok(f"已启动应用 {package}，Activity: {activity}")
        else:
            # 降级：用 monkey 启动（只发一个事件，通常会打开主界面）
            _run_adb(
                ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
                device_id=device_id,
            )
            logger.info("AndroidCtrl start_app (monkey) | package={}", package)
            return ToolResult.ok(f"已通过 monkey 启动应用 {package}")

    def _stop_app(self, package: str, device_id: str = None) -> ToolResult:
        """强制停止指定包名的应用。"""
        _run_adb(
            ["shell", "am", "force-stop", package],
            device_id=device_id,
        )
        logger.info("AndroidCtrl stop_app | package={}", package)
        return ToolResult.ok(f"已停止应用 {package}")

    def _get_focus(self, device_id: str = None) -> ToolResult:
        """获取当前前台 Activity（判断哪个应用正在运行）。"""
        output = _run_adb(
            ["shell", "dumpsys", "window"],
            device_id=device_id,
            timeout=10,
        ).decode("utf-8", errors="replace")

        # 查找 mCurrentFocus 行，格式：mCurrentFocus=Window{... package/Activity}
        current_focus = None
        for line in output.splitlines():
            line = line.strip()
            if "mCurrentFocus" in line:
                current_focus = line
                break

        if current_focus:
            logger.info("AndroidCtrl get_focus | {}", current_focus[:120])
            return ToolResult.ok(current_focus)
        return ToolResult.ok("未能获取当前前台 Activity（dumpsys window 输出无 mCurrentFocus）")

    def _tap(self, x: int, y: int, device_id: str = None) -> ToolResult:
        """模拟屏幕点击。"""
        _run_adb(
            ["shell", "input", "tap", str(x), str(y)],
            device_id=device_id,
        )
        logger.info("AndroidCtrl tap | x={} y={} device={}", x, y, device_id or "default")
        return ToolResult.ok(f"已点击坐标 ({x}, {y})")

    def _swipe(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int,
        device_id: str = None,
    ) -> ToolResult:
        """模拟屏幕滑动。"""
        _run_adb(
            ["shell", "input", "swipe",
             str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
            device_id=device_id,
        )
        logger.info(
            "AndroidCtrl swipe | ({},{}) -> ({},{}) {}ms device={}",
            x1, y1, x2, y2, duration_ms, device_id or "default"
        )
        return ToolResult.ok(f"已滑动：({x1}, {y1}) → ({x2}, {y2})，时长 {duration_ms}ms")

    def _keyevent(self, key: str, device_id: str = None) -> ToolResult:
        """发送按键事件。"""
        # 通用别名映射（与 input_android.py 保持一致）
        aliases = {
            "back":    "KEYCODE_BACK",
            "home":    "KEYCODE_HOME",
            "enter":   "KEYCODE_ENTER",
            "escape":  "KEYCODE_ESCAPE",
            "delete":  "KEYCODE_DEL",
            "tab":     "KEYCODE_TAB",
            "space":   "KEYCODE_SPACE",
            "up":      "KEYCODE_DPAD_UP",
            "down":    "KEYCODE_DPAD_DOWN",
            "left":    "KEYCODE_DPAD_LEFT",
            "right":   "KEYCODE_DPAD_RIGHT",
        }
        keycode = aliases.get(key.lower(), key.upper())
        _run_adb(
            ["shell", "input", "keyevent", keycode],
            device_id=device_id,
        )
        logger.info("AndroidCtrl keyevent | key={} → {} device={}", key, keycode, device_id or "default")
        return ToolResult.ok(f"已发送按键：{key}（{keycode}）")

    def _shell(self, command: str, device_id: str = None) -> ToolResult:
        """执行任意 adb shell 命令（高级用途）。"""
        # 将命令字符串拆为参数列表传给 shell
        output = _run_adb(
            ["shell"] + command.split(),
            device_id=device_id,
            timeout=30,
        ).decode("utf-8", errors="replace")

        # 截断过长输出
        if len(output) > _MAX_OUTPUT_LEN:
            output = output[:_MAX_OUTPUT_LEN] + f"\n... (输出已截断，共 {len(output)} 字符)"

        logger.info("AndroidCtrl shell | cmd={!r} output_len={}", command[:80], len(output))
        return ToolResult.ok(output or "(命令执行成功，无输出)")

    def _ui_dump(self, device_id: str = None) -> ToolResult:
        """
        获取当前 Android 界面的 UI 节点树（uiautomator dump）。

        视觉识别不可用时的结构化替代方案：无需截图即可知道界面有哪些元素、
        各元素的文本内容、坐标和是否可点击。

        实现步骤：
            1. 用 uiautomator dump 把当前 UI 层级导出为 XML 文件（保存在设备上）
            2. 用 adb pull 或 exec-out cat 拉取 XML 内容
            3. 解析 XML，提取 text/content-desc/bounds/class/clickable 等属性
            4. 返回可读的节点列表（只保留有 text 或可点击的有效节点，过滤空噪音）

        Returns:
            ToolResult.ok() 包含 JSON 格式的节点列表，字段：
                text:         元素文本（可能为空）
                content_desc: 无障碍描述（text 为空时的备用标签）
                class_name:   控件类名（如 android.widget.Button）
                clickable:    是否可点击（bool）
                bounds:       坐标 {left, top, right, bottom}
                center:       中心坐标 [x, y]（可直接用于 tap action）
        """
        # 设备上的 dump 输出路径（临时文件）
        DUMP_PATH = "/sdcard/window_dump.xml"

        try:
            # 第一步：执行 uiautomator dump（耗时约 1~3 秒）
            _run_adb(
                ["shell", "uiautomator", "dump", DUMP_PATH],
                device_id=device_id,
                timeout=15,
            )

            # 第二步：把 XML 内容读取到本地（exec-out cat 避免 pull 的路径问题）
            xml_bytes = _run_adb(
                ["shell", "cat", DUMP_PATH],
                device_id=device_id,
                timeout=10,
            )
            xml_str = xml_bytes.decode("utf-8", errors="replace")

        except RuntimeError as e:
            return ToolResult.error(f"uiautomator dump 失败：{e}")

        # 第三步：解析 XML，提取节点属性
        nodes = self._parse_ui_xml(xml_str)

        logger.info("AndroidCtrl ui_dump | count={} device={}", len(nodes), device_id or "default")

        if not nodes:
            return ToolResult.ok("当前界面没有检测到可交互的 UI 元素。")

        lines = [f"当前界面共检测到 {len(nodes)} 个有效 UI 元素：\n"]
        for i, n in enumerate(nodes, 1):
            label = n.get("text") or n.get("content_desc") or "（无文本）"
            cls = n.get("class_name", "").split(".")[-1]  # 只取类名末段，如 Button
            clickable = "可点击" if n.get("clickable") else "不可点击"
            cx, cy = n.get("center", [0, 0])
            b = n.get("bounds", {})
            lines.append(
                f"[{i}] 文本：{label}，控件类型：{cls}，{clickable}，"
                f"中心坐标：({cx}, {cy})，"
                f"区域：左{b.get('left','?')} 上{b.get('top','?')} 右{b.get('right','?')} 下{b.get('bottom','?')}"
            )
        return ToolResult.ok("\n".join(lines))

    def _parse_ui_xml(self, xml_str: str) -> list[dict]:
        """
        解析 uiautomator dump 输出的 XML，返回有效节点列表。

        只保留满足以下任一条件的节点（过滤掉无意义的空容器）：
            - text 属性非空
            - content-desc 属性非空
            - clickable="true"

        Args:
            xml_str: uiautomator dump 生成的 XML 字符串

        Returns:
            节点字典列表，每条包含 text/content_desc/class_name/clickable/bounds/center。
        """
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as e:
            logger.warning("UI XML 解析失败: {}", e)
            return []

        nodes = []
        # 遍历所有节点（不限层级）
        for elem in root.iter():
            text = (elem.get("text") or "").strip()
            content_desc = (elem.get("content-desc") or "").strip()
            clickable = elem.get("clickable", "false").lower() == "true"
            class_name = elem.get("class", "")
            bounds_str = elem.get("bounds", "")

            # 过滤：text/content_desc 均为空且不可点击 → 跳过
            if not text and not content_desc and not clickable:
                continue

            # 解析 bounds：格式为 "[left,top][right,bottom]"
            bounds = self._parse_bounds(bounds_str)
            if bounds is None:
                continue

            left, top, right, bottom = bounds
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2

            nodes.append({
                "text":         text,
                "content_desc": content_desc,
                "class_name":   class_name,
                "clickable":    clickable,
                "bounds": {
                    "left":   left,
                    "top":    top,
                    "right":  right,
                    "bottom": bottom,
                },
                "center": [center_x, center_y],
            })

        return nodes

    def _parse_bounds(self, bounds_str: str) -> tuple[int, int, int, int] | None:
        """
        解析 uiautomator 的 bounds 字符串。

        输入格式："[left,top][right,bottom]"，如 "[0,0][1080,1920]"

        Args:
            bounds_str: bounds 属性字符串

        Returns:
            (left, top, right, bottom) 整数元组，解析失败返回 None。
        """
        import re
        # 匹配 "[left,top][right,bottom]" 格式
        match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
        if not match:
            return None
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
        )

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _find_main_activity(self, package: str, device_id: str = None) -> str | None:
        """
        查找应用的主 Activity（MAIN + LAUNCHER）。

        通过 dumpsys package 输出中查找带有
        android.intent.action.MAIN 的 Activity。

        Args:
            package:   应用包名
            device_id: 设备序列号

        Returns:
            Activity 类名（如 ".MainActivity"），找不到返回 None。
        """
        try:
            output = _run_adb(
                ["shell", "dumpsys", "package", package],
                device_id=device_id,
                timeout=10,
            ).decode("utf-8", errors="replace")
        except RuntimeError:
            return None

        # 查找 MAIN activity 行，格式示例：
        # com.example.app/com.example.app.MainActivity filter ...
        #   Action: "android.intent.action.MAIN"
        lines = output.splitlines()
        for i, line in enumerate(lines):
            # 找到含 MAIN 的行
            if "android.intent.action.MAIN" in line:
                # 往前找最近的 activity 声明行（含 package/ClassName 格式）
                for j in range(i, max(i - 10, 0) - 1, -1):
                    candidate = lines[j].strip()
                    if "/" in candidate and package in candidate:
                        # 提取 Activity 名：package/ActivityClass → ActivityClass
                        parts = candidate.split("/")
                        if len(parts) >= 2:
                            activity = parts[1].split()[0]  # 去掉行尾多余内容
                            return activity
        return None
