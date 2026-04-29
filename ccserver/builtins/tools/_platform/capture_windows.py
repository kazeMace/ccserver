"""
_platform/capture_windows — Windows 截图适配。

使用 mss（跨平台，速度快），可选 win32api 实现窗口精确截取。

依赖：
    pip install mss pillow
    # 可选：pip install pywin32（窗口标题截图）
"""

import io
from loguru import logger


def capture(region: list | None = None) -> bytes:
    """
    截取 Windows 屏幕，返回 PNG bytes。

    Args:
        region: [left, top, width, height] 截取区域，None 表示全屏。

    Returns:
        PNG 格式图像的 bytes。
    """
    try:
        import mss
    except ImportError:
        raise RuntimeError(
            "截图依赖 mss 未安装，请运行：conda run -n ccserver pip install mss pillow"
        )

    with mss.mss() as sct:
        if region is not None:
            assert len(region) == 4, "region 格式须为 [left, top, width, height]"
            mon = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
        else:
            mon = sct.monitors[1]

        screenshot = sct.grab(mon)
        from PIL import Image
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        logger.debug("Windows 截图完成 | size={}x{} region={}", screenshot.width, screenshot.height, region)
        return buf.getvalue()


def capture_window(title: str) -> bytes:
    """
    截取 Windows 上指定标题的窗口。

    使用 win32gui 查找窗口句柄，win32ui / PIL 做截图。
    降级到全屏截图。

    Args:
        title: 窗口标题关键词（部分匹配）。

    Returns:
        PNG 格式图像的 bytes。
    """
    try:
        import win32gui
        import win32ui
        import win32con
        from PIL import Image
        import ctypes

        # 查找窗口句柄（部分标题匹配）
        hwnd = None

        def enum_callback(h, _):
            nonlocal hwnd
            win_title = win32gui.GetWindowText(h)
            if title.lower() in win_title.lower() and win32gui.IsWindowVisible(h):
                hwnd = h
                return False  # 停止枚举
            return True

        win32gui.EnumWindows(enum_callback, None)

        if hwnd is None:
            logger.warning("未找到窗口 title={}，降级为全屏截图", title)
            return capture()

        # 获取窗口尺寸（DPI 感知）
        ctypes.windll.user32.SetProcessDPIAware()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

        bmp_info = bitmap.GetInfo()
        bmp_str = bitmap.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]), bmp_str, "raw", "BGRX", 0, 1)

        # 清理 GDI 资源
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        win32ui.DeleteObject(bitmap.GetHandle())

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        logger.debug("Windows 窗口截图完成 | title={} size={}x{}", title, width, height)
        return buf.getvalue()

    except ImportError:
        logger.warning("pywin32 未安装，降级为全屏截图")
        return capture()
    except Exception as e:
        logger.warning("Windows 窗口截图失败 title={} error={}，降级为全屏截图", title, e)
        return capture()


def capture_by_app(app_name: str) -> bytes:
    """
    按进程名截取 Windows 窗口。

    通过枚举所有可见窗口，找到进程名包含 app_name 的窗口，
    选面积最大的（主窗口）截图。

    Args:
        app_name: 进程名关键词，例如 "chrome"、"notepad"、"wechat"。
                  大小写不敏感，支持部分匹配。

    Returns:
        PNG 格式图像的 bytes。找不到时降级为全屏截图。
    """
    try:
        import win32gui
        import win32process
        import win32api
        import psutil
    except ImportError:
        logger.warning("pywin32 或 psutil 未安装，降级为全屏截图")
        return capture()

    try:
        # 枚举所有可见且有标题的窗口
        candidates = []

        def enum_callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if not win32gui.GetWindowText(hwnd):
                return True

            # 通过窗口句柄获取进程 ID，再获取进程名
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid)
                proc_name = proc.name()
            except Exception:
                return True

            if app_name.lower() in proc_name.lower():
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                area = (right - left) * (bottom - top)
                if area > 0:
                    candidates.append((area, hwnd, proc_name))
            return True

        win32gui.EnumWindows(enum_callback, None)

        if not candidates:
            logger.warning("未找到进程窗口 app={}，降级为全屏截图", app_name)
            return capture()

        # 取面积最大的窗口（主窗口）
        candidates.sort(key=lambda item: item[0], reverse=True)
        target_hwnd = candidates[0][1]
        proc_name = candidates[0][2]
        logger.info("匹配到进程窗口 | app={} proc={} hwnd={}", app_name, proc_name, target_hwnd)

        return _capture_hwnd(target_hwnd)

    except Exception as e:
        logger.warning("按进程名截图失败 app={} error={}，降级为全屏截图", app_name, e)
        return capture()


def _capture_hwnd(hwnd: int) -> bytes:
    """
    通过窗口句柄（HWND）截取 Windows 窗口，返回 PNG bytes。

    这是所有 Windows 窗口截图方式的最终执行层。

    Args:
        hwnd: Windows 窗口句柄。

    Returns:
        PNG 格式图像的 bytes。

    Raises:
        RuntimeError: 截图失败。
    """
    import win32gui
    import win32ui
    import win32con
    from PIL import Image
    import ctypes

    # DPI 感知，避免高分屏坐标偏移
    ctypes.windll.user32.SetProcessDPIAware()

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        raise RuntimeError(f"窗口尺寸无效 hwnd={hwnd} size={width}x{height}")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bitmap)
    save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

    bmp_info = bitmap.GetInfo()
    bmp_str = bitmap.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        bmp_str, "raw", "BGRX", 0, 1,
    )

    # 释放 GDI 资源（必须手动释放，否则泄漏）
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    win32ui.DeleteObject(bitmap.GetHandle())

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    logger.debug("Windows HWND 截图完成 | hwnd={} size={}x{}", hwnd, width, height)
    return buf.getvalue()
