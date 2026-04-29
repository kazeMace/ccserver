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
        "窗口截图优先级：bundle_id（最精确）> app_name（最稳定）> window_title（标题易变）> 全屏。"
        "可选 region=[left,top,width,height] 只截取部分区域，减少 token 消耗。"
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
        "region": ToolParam(
            type="array",
            description=(
                "截取区域，格式 [left, top, width, height]（像素坐标）。"
                "例如 [0, 0, 800, 600] 截取左上角 800x600 区域。"
                "省略或 null 表示全屏截取。"
            ),
            required=False,
            items={"type": "integer"},
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
        region: list | None = None,
        window_title: str | None = None,
        app_name: str | None = None,
        bundle_id: str | None = None,
        device_id: str | None = None,
    ) -> ToolResult:
        """
        执行截图，返回多模态 ToolResult。

        纯系统操作：截取屏幕像素，生成缩略图，返回图像 + 文本描述。
        不包含任何 AI 理解（OCR / VLM 描述请使用单独的视觉工具）。

        Args:
            target:       截图目标，"desktop" 或 "android"
            region:       截取区域 [left, top, width, height]，None 为全屏
            window_title: 按窗口标题匹配（优先级最低）
            app_name:     按应用/进程名匹配（优先级中）
            bundle_id:    按 Bundle ID 匹配（仅 macOS，优先级最高）
            device_id:    Android 设备 ID

        Returns:
            ToolResult.multimodal([image_block, thumbnail_block, text_block])
        """
        assert target in ("desktop", "android"), f"target 必须是 desktop 或 android，得到: {target}"

        try:
            # ── 1. 截图 ──────────────────────────────────────────────────────
            png_bytes = self._capture(target, region, window_title, app_name, bundle_id, device_id)

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
            description_text = (
                f"截图完成。分辨率：{orig_w}x{orig_h}（缩放后 {scaled_w}x{scaled_h}），"
                f"平台：{platform_name}。"
            )

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
    ) -> bytes:
        """
        根据 target、平台和窗口匹配参数分发到具体截图实现。

        窗口截图优先级（desktop 模式）：
            bundle_id → app_name → window_title → 全屏

        Args:
            target:       "desktop" 或 "android"
            region:       截取区域，None 为全屏
            window_title: 按窗口标题匹配（优先级最低）
            app_name:     按应用/进程名匹配（优先级中）
            bundle_id:    按 Bundle ID 匹配（仅 macOS，优先级最高）
            device_id:    Android 设备序列号

        Returns:
            PNG bytes
        """
        # Android：不支持窗口匹配，直接全屏或区域截图
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
            return capture_macos.capture(region=region)

        elif system == "Windows":
            from .._platform import capture_windows
            # Windows 没有 bundle_id 概念，bundle_id 参数忽略
            if app_name:
                return capture_windows.capture_by_app(app_name)
            if window_title:
                return capture_windows.capture_window(window_title)
            return capture_windows.capture(region=region)

        else:
            # Linux：只支持全屏/区域截图（mss 方式，需 X11）
            try:
                from .._platform import capture_macos  # mss 实现与 macOS 相同
                return capture_macos.capture(region=region)
            except Exception as e:
                raise RuntimeError(f"Linux 截图暂不完整支持，错误：{e}")

    def _platform_name(self, target: str) -> str:
        """返回人类可读的平台名称。"""
        if target == "android":
            return "Android"
        system = platform_module.system()
        return {"Darwin": "macOS", "Windows": "Windows"}.get(system, system)


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
