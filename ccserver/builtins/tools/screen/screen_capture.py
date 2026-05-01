"""
screen_capture — BTScreenCapture 截图工具（纯系统操作，无 AI 理解）。

支持 macOS、Windows、Android 三平台截图。
返回多模态 ToolResult（图像 base64 + 缩略图 + 文本描述），
Agent 可直接看到截图内容并做视觉决策。

截图内容的理解（OCR / VLM 描述 / 元素定位）由 extra_tools 提供，
本工具只负责「获取屏幕像素」这一个系统级操作。

依赖：
    pip install mss pillow           # macOS / Windows
    adb（PATH 中）                    # Android

使用示例（Agent 调用）：
    ScreenCapture()                  → 截取当前桌面全屏
    ScreenCapture(target="android")  → 截取默认 Android 设备
    ScreenCapture(region=[0,0,800,600])  → 截取左上角 800x600 区域
"""

import base64
import io
import platform as platform_module
from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools, ToolParam, ToolResult


# 缩略图最大边长（用于 TUI/飞书等低带宽渠道），单位像素
_THUMBNAIL_MAX_SIZE = 400
# 完整图像最大边长（节省 token），单位像素
_IMAGE_MAX_SIZE = 1280


class BTScreenCapture(BuiltinTools):
    """
    截取屏幕并返回图像，供 Agent 视觉理解和后续操作定位使用。

    返回多模态内容：图像 base64 + 缩略图 + 文本描述（分辨率、平台信息）。
    截图会自动缩放到最大 1280px（节省 token），并生成 400px 缩略图（供 WebUI 预览）。

    target 参数决定截图来源：
    - "desktop"：截取本机桌面（macOS 或 Windows）
    - "android"：通过 ADB 截取 Android 设备屏幕

    支持 region 参数限定截取区域，格式 [left, top, width, height]（像素坐标）。
    """

    name = "ScreenCapture"
    risk = "low"
    tags = ["screen", "read-only"]

    description = (
        "截取屏幕并返回图像，用于感知当前 GUI 状态。"
        "返回图像可直接用于视觉理解：识别界面元素、确认操作结果、分析游戏状态等。"
        "target='desktop' 截取本机桌面（macOS/Windows），target='android' 通过 ADB 截取手机屏幕。"
        "截图模式优先级：bundle_id（按窗口最精确）> app_name（按窗口稳定）> window_title（按窗口标题）"
        "> monitor（按显示器编号）> center+width+height（按中心点矩形）> region（按左上角矩形）> 全屏。"
        "返回 text block 包含：显示器列表、窗口桌面位置、坐标换算公式，供 agent 精确计算点击坐标。"
    )

    params = {
        "target": ToolParam(
            type="string",
            description=(
                "截图目标平台。"
                "'desktop'：本机桌面（macOS 或 Windows，自动检测）。"
                "'android'：Android 设备（通过 ADB，需连接并开启调试模式）。"
                "默认：'desktop'。"
            ),
            required=False,
            enum=["desktop", "android"],
        ),
        "monitor": ToolParam(
            type="integer",
            description=(
                "按显示器编号截图（仅 desktop 模式）。"
                "编号从 1 开始，1 通常为主屏。可先用 WindowList 或查看截图 text block 确认编号。"
                "示例：monitor=2 截取第二台显示器全屏。"
                "与 region/center/窗口参数互斥，优先级低于窗口参数，高于 region。"
            ),
            required=False,
        ),
        "region": ToolParam(
            type="array",
            description=(
                "按左上角坐标截取矩形区域，格式 [left, top, width, height]（桌面像素坐标）。"
                "例如 [100, 200, 800, 600] 截取从 (100,200) 起 800x600 的矩形。"
                "与 center 参数互斥；有窗口/monitor 参数时此参数忽略。"
            ),
            required=False,
            items={"type": "integer"},
        ),
        "center": ToolParam(
            type="array",
            description=(
                "按中心点坐标截取矩形区域，格式 [cx, cy]（桌面像素坐标）。"
                "必须与 width 和 height 参数一起使用。"
                "例如 center=[960,540], width=400, height=300 截取以 (960,540) 为中心的 400x300 矩形。"
                "与 region 互斥，优先级高于 region。有窗口/monitor 参数时此参数忽略。"
            ),
            required=False,
            items={"type": "integer"},
        ),
        "width": ToolParam(
            type="integer",
            description=(
                "配合 center 参数使用，指定截取区域的宽度（像素）。"
                "仅在提供 center 参数时生效。"
            ),
            required=False,
        ),
        "height": ToolParam(
            type="integer",
            description=(
                "配合 center 参数使用，指定截取区域的高度（像素）。"
                "height=width 时截取正方形区域。"
                "仅在提供 center 参数时生效。"
            ),
            required=False,
        ),
        "window_title": ToolParam(
            type="string",
            description=(
                "按窗口标题截取（仅 desktop 模式，部分匹配）。"
                "注意：很多游戏/模拟器窗口标题为空或动态变化，此时应改用 app_name。"
                "未找到时降级为全屏截图。"
            ),
            required=False,
        ),
        "app_name": ToolParam(
            type="string",
            description=(
                "按应用/进程名截取（仅 desktop 模式，比 window_title 更稳定）。"
                "macOS 匹配 kCGWindowOwnerName，Windows 匹配进程名。"
                "示例：'MuMu安卓设备'、'Google Chrome'、'WeChat'。"
                "未找到时降级为全屏截图。"
            ),
            required=False,
        ),
        "bundle_id": ToolParam(
            type="string",
            description=(
                "按 macOS Bundle ID 精确匹配截取（仅 macOS desktop 模式）。"
                "Bundle ID 不受应用名/标题变化影响，是最精确的定位方式。"
                "示例：'com.netease.mumu.nemux.emulator'、'com.google.Chrome'。"
                "未找到时降级为全屏截图。"
            ),
            required=False,
        ),
        "device_id": ToolParam(
            type="string",
            description=(
                "Android 设备序列号（target='android' 且多台设备时指定）。"
                "可通过 AndroidCtrl(action='list_devices') 获取。"
                "省略时使用默认连接的设备。"
            ),
            required=False,
        ),
    }

    async def run(
        self,
        target: str = "desktop",
        monitor: int | None = None,
        region: list | None = None,
        center: list | None = None,
        width: int | None = None,
        height: int | None = None,
        window_title: str | None = None,
        app_name: str | None = None,
        bundle_id: str | None = None,
        device_id: str | None = None,
    ) -> ToolResult:
        """
        执行截图，返回多模态 ToolResult。

        参数优先级（高优先级会覆盖低优先级）：
            bundle_id > app_name > window_title > monitor > center > region > 全屏

        Args:
            target:       截图目标，"desktop" 或 "android"
            monitor:      按显示器编号截图（1=主屏），仅 desktop
            region:       按左上角矩形截图 [left, top, width, height]
            center:       按中心点截图 [cx, cy]，需配合 width/height
            width:        center 模式下的截取宽度
            height:       center 模式下的截取高度
            window_title: 按窗口标题匹配（优先级低于 app_name）
            app_name:     按应用/进程名匹配
            bundle_id:    按 Bundle ID 匹配（仅 macOS，优先级最高）
            device_id:    Android 设备 ID

        Returns:
            ToolResult.multimodal([image_block, thumbnail_block, text_block])
        """
        assert target in ("desktop", "android"), f"target 必须是 desktop 或 android，得到: {target}"

        # center 模式：将 center+width+height 转换为 region
        resolved_region = region
        if center is not None and not (bundle_id or app_name or window_title or monitor):
            assert len(center) == 2, "center 格式须为 [cx, cy]"
            assert width and height, "使用 center 参数时必须同时提供 width 和 height"
            cx, cy = center
            left = cx - width // 2
            top = cy - height // 2
            resolved_region = [left, top, width, height]
            logger.info("center 模式 | center=({},{}) size={}x{} → region={}", cx, cy, width, height, resolved_region)

        try:
            # ── 1. 截图 ──────────────────────────────────────────────────────
            png_bytes = self._capture(
                target, resolved_region, window_title, app_name, bundle_id, device_id, monitor
            )

            # ── 2. 获取图像尺寸 ───────────────────────────────────────────────
            from PIL import Image
            img = Image.open(io.BytesIO(png_bytes))
            orig_w, orig_h = img.size

            # ── 3. 缩放完整图像（节省 token） ──────────────────────────────────
            img_scaled = _resize_keep_ratio(img, _IMAGE_MAX_SIZE)
            main_b64 = _to_base64(img_scaled)

            # ── 4. 生成缩略图（供 WebUI / 飞书等低带宽渠道预览） ──────────────
            img_thumb = _resize_keep_ratio(img, _THUMBNAIL_MAX_SIZE)
            thumb_b64 = _to_base64(img_thumb)

            scaled_w, scaled_h = img_scaled.size
            logger.info(
                "截图完成 | target={} orig={}x{} scaled={}x{} device={}",
                target, orig_w, orig_h, scaled_w, scaled_h, device_id or "default"
            )

            platform_name = self._platform_name(target)

            # ── 5a. 查询显示器 + 窗口几何信息，拼进 text block ────────────────
            geo_lines = self._build_geo_text(
                target, resolved_region, window_title, app_name, bundle_id,
                orig_w, orig_h, scaled_w, scaled_h, platform_name,
                monitor=monitor, center=center, width=width, height=height,
            )
            description_text = "\n".join(geo_lines)

            # ── 5. 组装 ToolResult ────────────────────────────────────────────
            image_block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": main_b64,
                },
            }

            thumb_block = {
                "type": "image_thumbnail",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": thumb_b64,
                },
                "_meta": {
                    "orig_width": orig_w,
                    "orig_height": orig_h,
                    "platform": platform_name,
                },
            }

            text_block = {"type": "text", "text": description_text}

            return ToolResult.multimodal([image_block, thumb_block, text_block])

        except RuntimeError as e:
            # 依赖缺失或 ADB 失败等可预期错误
            return ToolResult.error(str(e))
        except Exception as e:
            logger.exception("截图异常 | target={} error={}", target, e)
            return ToolResult.error(f"截图失败：{e}")

    def _capture(
        self,
        target: str,
        region: list | None,
        window_title: str | None,
        app_name: str | None,
        bundle_id: str | None,
        device_id: str | None,
        monitor: int | None = None,
    ) -> bytes:
        """
        根据 target、平台和窗口匹配参数分发到具体截图实现。

        参数优先级：bundle_id → app_name → window_title → monitor → region → 全屏

        Args:
            target:       "desktop" 或 "android"
            region:       截取区域 [left, top, width, height]，None 为全屏
            window_title: 按窗口标题匹配（优先级最低）
            app_name:     按应用/进程名匹配
            bundle_id:    按 Bundle ID 匹配（仅 macOS，优先级最高）
            device_id:    Android 设备序列号
            monitor:      按显示器编号截图（1=主屏），仅 desktop

        Returns:
            PNG bytes
        """
        # Android：不支持窗口/显示器匹配，直接全屏或区域截图
        if target == "android":
            from .._platform import capture_android
            return capture_android.capture(region=region, device_id=device_id)

        # desktop：按当前操作系统选择实现
        system = platform_module.system()

        if system == "Darwin":
            from .._platform import capture_macos
            # 按优先级依次尝试窗口匹配
            if bundle_id:
                return capture_macos.capture_by_bundle_id(bundle_id)
            if app_name:
                return capture_macos.capture_by_app(app_name)
            if window_title:
                return capture_macos.capture_window(window_title)
            # 按显示器编号截图：转换为 region
            if monitor is not None:
                mon_region = _monitor_to_region(monitor)
                return capture_macos.capture(region=mon_region)
            return capture_macos.capture(region=region)

        elif system == "Windows":
            from .._platform import capture_windows
            # Windows 没有 bundle_id 概念，bundle_id 参数忽略
            if app_name:
                return capture_windows.capture_by_app(app_name)
            if window_title:
                return capture_windows.capture_window(window_title)
            if monitor is not None:
                mon_region = _monitor_to_region(monitor)
                return capture_windows.capture(region=mon_region)
            return capture_windows.capture(region=region)

        else:
            # Linux：只支持全屏/区域截图（mss 方式，需 X11）
            try:
                from .._platform import capture_macos  # mss 实现与 macOS 相同
                if monitor is not None:
                    mon_region = _monitor_to_region(monitor)
                    return capture_macos.capture(region=mon_region)
                return capture_macos.capture(region=region)
            except Exception as e:
                raise RuntimeError(f"Linux 截图暂不完整支持，错误：{e}")

    def _platform_name(self, target: str) -> str:
        """返回人类可读的平台名称。"""
        if target == "android":
            return "Android"
        system = platform_module.system()
        return {"Darwin": "macOS", "Windows": "Windows"}.get(system, system)

    def _build_geo_text(
        self,
        target: str,
        region: list | None,
        window_title: str | None,
        app_name: str | None,
        bundle_id: str | None,
        orig_w: int,
        orig_h: int,
        scaled_w: int,
        scaled_h: int,
        platform_name: str,
        monitor: int | None = None,
        center: list | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> list[str]:
        """
        构建截图的几何信息文本行，供 agent 精确计算元素坐标。

        包含：截图基础信息、截图模式说明、全部显示器列表、
              截图窗口的桌面位置、坐标换算公式。

        Args:
            target:      "desktop" 或 "android"
            region:      实际截取区域（已含 center→region 转换结果）
            window_title/app_name/bundle_id: 窗口匹配参数
            orig_w/orig_h:    原始图像尺寸
            scaled_w/scaled_h: 缩放后图像尺寸
            platform_name: 平台名称字符串
            monitor:     按显示器编号截图时的编号
            center:      按中心点截图时的原始 center 参数
            width/height: 按中心点截图时的原始尺寸参数

        Returns:
            字符串行列表，join 后为完整 text block 内容。
        """
        lines = []

        # ── 第一行：截图基础信息 ──────────────────────────────────────────────
        lines.append(
            f"截图完成。原始分辨率：{orig_w}x{orig_h}（发送给模型的缩放版：{scaled_w}x{scaled_h}），"
            f"平台：{platform_name}。"
        )

        # Android：无桌面显示器信息，直接返回
        if target == "android":
            lines.append("Android 截图，无桌面显示器信息。")
            return lines

        system = platform_module.system()
        if system not in ("Darwin", "Windows"):
            return lines

        # ── 第二步：查询所有显示器 ────────────────────────────────────────────
        monitors = []
        try:
            if system == "Darwin":
                from .._platform.window_macos import _get_monitors
            else:
                from .._platform.window_windows import _get_monitors

            monitors = _get_monitors()
            if monitors:
                mon_descs = []
                for m in monitors:
                    b = m["bounds"]
                    primary = "（主屏）" if m.get("is_primary") else ""
                    mon_descs.append(
                        f"显示器{m['index']}{primary}：左上({b['x']},{b['y']}) "
                        f"分辨率{b['width']}x{b['height']}"
                    )
                lines.append(f"共 {len(monitors)} 台显示器：" + "；".join(mon_descs) + "。")
        except Exception as e:
            logger.debug("获取显示器信息失败: {}", e)

        # ── 第三步：说明本次截图的模式和坐标语义 ──────────────────────────────
        has_window_param = bool(bundle_id or app_name or window_title)

        if has_window_param:
            # 窗口截图：查询窗口几何信息并给出换算公式
            try:
                if system == "Darwin":
                    from .._platform.window_macos import get_window_info
                else:
                    from .._platform.window_windows import get_window_info

                info = get_window_info(
                    app_name=app_name,
                    window_title=window_title,
                    bundle_id=bundle_id,
                )

                if "error" in info:
                    lines.append(f"窗口信息查询失败：{info['error']}。图像坐标可能为全屏坐标。")
                    return lines

                win = info.get("window", {})
                b = win.get("bounds", {})
                wx, wy = b.get("x", "?"), b.get("y", "?")
                ww, wh = b.get("width", "?"), b.get("height", "?")
                cx, cy = win.get("center", ["?", "?"])
                owner = win.get("owner", "?")
                title = win.get("title") or "（无标题）"
                is_fg = win.get("is_foreground", False)
                bid = win.get("bundle_id", "")

                placement = info.get("placement", {})
                on_monitor = placement.get("on_monitor")
                spanned = placement.get("monitors_spanned", [])

                lines.append(
                    f"截图模式：窗口截图。"
                    f"进程「{owner}」，标题「{title}」"
                    + (f"，Bundle ID：{bid}" if bid else "")
                    + f"，当前{'在前台' if is_fg else '在后台'}。"
                )
                lines.append(
                    f"窗口在桌面的位置：左上角 ({wx}, {wy})，"
                    f"宽 {ww} 像素，高 {wh} 像素，中心点 ({cx}, {cy})。"
                )
                if on_monitor is not None:
                    lines.append(f"窗口主显示器：显示器{on_monitor}。")
                if len(spanned) > 1:
                    lines.append(f"窗口跨越显示器：{spanned}（跨 {len(spanned)} 屏）。")
                lines.append(
                    f"坐标换算：图像坐标 (ix, iy) → 桌面绝对坐标 (ix + {wx}, iy + {wy})。"
                )

            except Exception as e:
                logger.debug("获取窗口几何信息失败: {}", e)
                lines.append(f"窗口几何信息查询失败（{e}），图像坐标可能为全屏坐标。")

        elif monitor is not None:
            # 按显示器编号截图
            mon_info = next((m for m in monitors if m["index"] == monitor), None)
            if mon_info:
                b = mon_info["bounds"]
                lines.append(
                    f"截图模式：显示器{monitor}全屏截图"
                    + ("（主屏）" if mon_info.get("is_primary") else "") + "。"
                    f"显示器左上角桌面坐标：({b['x']}, {b['y']})，"
                    f"分辨率：{b['width']}x{b['height']}。"
                )
                lines.append(
                    f"坐标换算：图像坐标 (ix, iy) → 桌面绝对坐标 (ix + {b['x']}, iy + {b['y']})。"
                )
            else:
                lines.append(f"截图模式：显示器{monitor}截图（未能查到该显示器信息）。图像坐标 = 桌面绝对坐标。")

        elif center is not None and region is not None:
            # 按中心点截图（center 已被转换为 region）
            cx_orig, cy_orig = center
            lines.append(
                f"截图模式：中心点矩形截图。"
                f"中心点 ({cx_orig}, {cy_orig})，尺寸 {width}x{height}，"
                f"实际截取区域：左上({region[0]},{region[1]}) 宽{region[2]}x高{region[3]}。"
                f"图像坐标 = 桌面绝对坐标，无需换算。"
            )

        elif region is not None:
            # 按左上角矩形截图
            lines.append(
                f"截图模式：矩形区域截图。"
                f"截取区域：左上({region[0]},{region[1]}) 宽{region[2]}x高{region[3]}。"
                f"图像坐标 = 桌面绝对坐标，无需换算。"
            )

        else:
            # 全屏截图
            lines.append("截图模式：全屏截图。图像坐标 = 桌面绝对坐标，无需换算。")

        return lines


# ── 显示器工具函数 ────────────────────────────────────────────────────────────


def _monitor_to_region(monitor_index: int) -> list[int] | None:
    """
    将显示器编号转换为 region [left, top, width, height]。

    使用 mss 获取显示器信息，monitors[0] 是所有显示器合并，从 1 开始才是单显示器。

    Args:
        monitor_index: 显示器编号，从 1 开始。

    Returns:
        [left, top, width, height] 列表，找不到时返回 None（调用方降级为全屏）。
    """
    try:
        import mss
        with mss.mss() as sct:
            # sct.monitors[0] 是所有显示器合并，[1] 起才是单个显示器
            if monitor_index < 1 or monitor_index >= len(sct.monitors):
                logger.warning(
                    "_monitor_to_region | 显示器编号 {} 超出范围（共 {} 台），降级为全屏",
                    monitor_index, len(sct.monitors) - 1
                )
                return None
            mon = sct.monitors[monitor_index]
            region = [mon["left"], mon["top"], mon["width"], mon["height"]]
            logger.info(
                "_monitor_to_region | monitor={} → region={}",
                monitor_index, region
            )
            return region
    except ImportError:
        logger.warning("mss 未安装，无法按显示器截图")
        return None
    except Exception as e:
        logger.warning("_monitor_to_region 失败 monitor={} error={}", monitor_index, e)
        return None


# ── 图像处理工具函数 ───────────────────────────────────────────────────────────


def _resize_keep_ratio(img, max_size: int):
    """
    等比缩放图像，确保最长边不超过 max_size。

    Args:
        img:      PIL Image 对象
        max_size: 最长边像素上限

    Returns:
        缩放后的 PIL Image（原图不超过 max_size 时原样返回）
    """
    from PIL import Image
    w, h = img.size
    if max(w, h) <= max_size:
        return img
    ratio = max_size / max(w, h)
    new_w = int(w * ratio)
    new_h = int(h * ratio)
    return img.resize((new_w, new_h), Image.LANCZOS)


def _to_base64(img) -> str:
    """
    将 PIL Image 转换为 PNG base64 字符串。

    Args:
        img: PIL Image 对象

    Returns:
        base64 编码的 PNG 字符串
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")
