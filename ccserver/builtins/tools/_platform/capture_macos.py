"""
_platform/capture_macos — macOS 截图适配。

提供四种截图模式：
  1. capture(region)                 — 全屏或区域截图（mss）
  2. capture_window(title)           — 按窗口标题匹配截图（Quartz）
  3. capture_by_app(app_name)        — 按进程/应用名匹配截图（Quartz，最稳定）
  4. capture_by_bundle_id(bundle_id) — 按 Bundle ID 精确匹配截图（Quartz，最精确）

优先级：bundle_id > app_name > title > 全屏

依赖：
    pip install mss pillow
    # 可选（窗口截图必须）：pip install pyobjc-framework-Quartz pyobjc-framework-AppKit
"""

import io
from loguru import logger


def capture(region: list | None = None) -> bytes:
    """
    截取 macOS 屏幕，返回 PNG bytes。

    Args:
        region: [left, top, width, height] 截取区域，None 表示全屏。

    Returns:
        PNG 格式图像的 bytes。

    Raises:
        RuntimeError: 截图失败或 mss 未安装。
    """
    try:
        import mss
        import mss.tools
    except ImportError:
        raise RuntimeError(
            "截图依赖 mss 未安装，请运行：conda run -n ccserver pip install mss pillow"
        )

    with mss.mss() as sct:
        if region is not None:
            # region 格式：[left, top, width, height]
            assert len(region) == 4, "region 格式须为 [left, top, width, height]"
            mon = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
        else:
            # 截取主显示器
            mon = sct.monitors[1]  # monitors[0] 是所有显示器合并，[1] 是主屏

        screenshot = sct.grab(mon)

        # mss 返回 BGRA 格式，转换为 PNG bytes
        from PIL import Image
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        logger.debug("macOS 截图完成 | size={}x{} region={}", screenshot.width, screenshot.height, region)
        return buf.getvalue()


def capture_window(title: str) -> bytes:
    """
    截取 macOS 上指定标题的窗口。

    尝试使用 Quartz（原生 API），降级到全屏截图。

    Args:
        title: 窗口标题关键词（部分匹配）。

    Returns:
        PNG 格式图像的 bytes。
    """
    try:
        import Quartz  # noqa: F401
        import Quartz.CoreGraphics as CG
        from PIL import Image  # noqa: F401
        window_list = CG.CGWindowListCopyWindowInfo(
            CG.kCGWindowListOptionOnScreenOnly | CG.kCGWindowListExcludeDesktopElements,
            CG.kCGNullWindowID,
        )

        target_id = None
        for win in window_list:
            win_title = win.get("kCGWindowName", "") or ""
            win_owner = win.get("kCGWindowOwnerName", "") or ""
            if title.lower() in win_title.lower() or title.lower() in win_owner.lower():
                target_id = win.get("kCGWindowNumber")
                break

        if target_id is None:
            logger.warning("未找到窗口 title={}，降级为全屏截图", title)
            return capture()

        # 截取指定窗口
        image_ref = CG.CGWindowListCreateImage(
            CG.CGRectNull,
            CG.kCGWindowListOptionIncludingWindow,
            target_id,
            CG.kCGWindowImageDefault,
        )
        width = CG.CGImageGetWidth(image_ref)
        height = CG.CGImageGetHeight(image_ref)
        bytes_per_row = CG.CGImageGetBytesPerRow(image_ref)

        data_provider = CG.CGImageGetDataProvider(image_ref)
        data = CG.CGDataProviderCopyData(data_provider)

        img = Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", bytes_per_row, 1)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        logger.debug("macOS 窗口截图完成 | title={} size={}x{}", title, width, height)
        return buf.getvalue()

    except ImportError:
        logger.warning("pyobjc-framework-Quartz 未安装，降级为全屏截图")
        return capture()
    except Exception as e:
        logger.warning("窗口截图失败 title={} error={}，降级为全屏截图", title, e)
        return capture()


def capture_by_app(app_name: str) -> bytes:
    """
    按应用进程名（kCGWindowOwnerName）截取 macOS 窗口。

    比 capture_window 更可靠：很多游戏/模拟器窗口标题为空或动态变化，
    但 owner（进程名）通常稳定不变。

    匹配策略：
    - 部分匹配 owner 名（大小写不敏感）
    - 多个窗口时选面积最大的（主窗口）
    - 自动过滤菜单栏等系统装饰窗口（高度 ≤ 40px 且宽度 > 2000px）

    Args:
        app_name: 应用/进程名关键词，例如 "MuMu安卓设备"、"Google Chrome"。

    Returns:
        PNG 格式图像的 bytes。降级为全屏截图时不报错。
    """
    try:
        import Quartz.CoreGraphics as CG
        from PIL import Image  # noqa: F401
    except ImportError:
        logger.warning("pyobjc-framework-Quartz 未安装，降级为全屏截图")
        return capture()

    try:
        window_list = CG.CGWindowListCopyWindowInfo(
            CG.kCGWindowListOptionOnScreenOnly | CG.kCGWindowListExcludeDesktopElements,
            CG.kCGNullWindowID,
        )

        # 筛选匹配的窗口，按面积降序排列，取最大的（主窗口）
        candidates = []
        for win in window_list:
            owner = win.get("kCGWindowOwnerName", "") or ""
            bounds = win.get("kCGWindowBounds", {})
            w = bounds.get("Width", 0)
            h = bounds.get("Height", 0)
            layer = win.get("kCGWindowLayer", 0)

            # 跳过菜单栏、状态栏等系统装饰窗口
            if h <= 40 and w > 2000:
                continue
            # 跳过不可见或零尺寸窗口
            if w <= 0 or h <= 0:
                continue
            # 只处理普通层级窗口（layer=0），跳过 Dock、菜单等
            if layer != 0:
                continue

            if app_name.lower() in owner.lower():
                candidates.append((w * h, win.get("kCGWindowNumber"), owner))

        if not candidates:
            logger.warning("未找到应用窗口 app={}，降级为全屏截图", app_name)
            return capture()

        # 面积最大的窗口即为主窗口
        candidates.sort(key=lambda item: item[0], reverse=True)
        target_id = candidates[0][1]
        owner_name = candidates[0][2]
        logger.info("匹配到应用窗口 | app={} owner={} window_id={}", app_name, owner_name, target_id)

        return _capture_quartz_window_by_id(target_id)

    except Exception as e:
        logger.warning("按应用名截图失败 app={} error={}，降级为全屏截图", app_name, e)
        return capture()


def capture_by_bundle_id(bundle_id: str) -> bytes:
    """
    按 macOS Bundle ID 精确匹配并截取窗口。

    Bundle ID 是 macOS 应用的唯一标识符，不受应用名/窗口标题变化影响，
    是最精确的窗口定位方式。

    例如：
        com.netease.mumu.nemux.emulator → MuMu 安卓设备画面
        com.google.Chrome               → Google Chrome

    Args:
        bundle_id: 应用的 Bundle ID，例如 "com.netease.mumu.nemux.emulator"。

    Returns:
        PNG 格式图像的 bytes。找不到时降级为全屏截图。
    """
    try:
        from AppKit import NSRunningApplication
    except ImportError:
        logger.warning("pyobjc-framework-AppKit 未安装，尝试 osascript 方式")
        return _capture_by_bundle_id_osascript(bundle_id)

    try:
        # 通过 Bundle ID 找到正在运行的应用实例
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
        if not apps or len(apps) == 0:
            logger.warning("Bundle ID {} 对应的应用未运行，降级为全屏截图", bundle_id)
            return capture()

        # 取第一个运行实例的进程名，再通过 app_name 方式截图
        app = apps[0]
        app_name = app.localizedName()
        logger.info("Bundle ID {} → 应用名 {}", bundle_id, app_name)
        return capture_by_app(app_name)

    except Exception as e:
        logger.warning("按 Bundle ID 截图失败 bundle_id={} error={}，降级为全屏截图", bundle_id, e)
        return capture()


def _capture_by_bundle_id_osascript(bundle_id: str) -> bytes:
    """
    通过 osascript 将 Bundle ID 解析为应用名，再截图。

    AppKit 不可用时的备选方案。

    Args:
        bundle_id: 应用 Bundle ID。

    Returns:
        PNG 格式图像的 bytes。
    """
    import subprocess
    try:
        result = subprocess.run(
            ["osascript", "-e", f'tell app id "{bundle_id}" to get name'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            app_name = result.stdout.strip()
            logger.info("osascript 解析 Bundle ID {} → {}", bundle_id, app_name)
            return capture_by_app(app_name)
    except Exception as e:
        logger.warning("osascript 解析失败 bundle_id={} error={}", bundle_id, e)

    logger.warning("Bundle ID {} 无法解析，降级为全屏截图", bundle_id)
    return capture()


def _capture_quartz_window_by_id(window_id: int) -> bytes:
    """
    通过 Quartz 窗口编号截取指定窗口，返回 PNG bytes。

    这是所有窗口截图方式的最终执行层。
    上层函数（capture_window/capture_by_app/capture_by_bundle_id）
    负责找到 window_id，此函数只负责截图。

    Args:
        window_id: Quartz 窗口编号（kCGWindowNumber）。

    Returns:
        PNG 格式图像的 bytes。

    Raises:
        RuntimeError: 截图失败。
    """
    import Quartz.CoreGraphics as CG
    from PIL import Image

    image_ref = CG.CGWindowListCreateImage(
        CG.CGRectNull,
        CG.kCGWindowListOptionIncludingWindow,
        window_id,
        CG.kCGWindowImageDefault,
    )

    if image_ref is None:
        raise RuntimeError(f"Quartz 窗口截图失败，window_id={window_id}")

    width = CG.CGImageGetWidth(image_ref)
    height = CG.CGImageGetHeight(image_ref)
    bytes_per_row = CG.CGImageGetBytesPerRow(image_ref)
    data_provider = CG.CGImageGetDataProvider(image_ref)
    data = CG.CGDataProviderCopyData(data_provider)

    # Quartz 返回 BGRA 格式，转换为 RGB PNG
    img = Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", bytes_per_row, 1)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")

    logger.debug("Quartz 窗口截图完成 | window_id={} size={}x{}", window_id, width, height)
    return buf.getvalue()
