"""
_platform/capture_android — Android 截图适配（通过 ADB）。

不依赖本地平台 API，通过 adb shell screencap 命令实现。
设备需要开启 USB 调试模式。

依赖：
    adb 命令行工具（需在 PATH 中）
    pip install pillow
"""

import io
import subprocess
from loguru import logger


def _run_adb(args: list[str], device_id: str | None = None, timeout: int = 10) -> bytes:
    """
    运行 adb 命令，返回 stdout bytes。

    Args:
        args:      adb 子命令参数列表，如 ["shell", "screencap", "-p"]
        device_id: 设备序列号（多设备时指定），None 使用默认设备
        timeout:   超时秒数

    Returns:
        命令 stdout 的 bytes。

    Raises:
        RuntimeError: adb 未安装或命令失败。
    """
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "adb 命令未找到，请安装 Android SDK Platform-Tools 并将 adb 加入 PATH"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"adb 命令超时（{timeout}s）: {' '.join(cmd)}")

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"adb 命令失败 (exit={result.returncode}): {err}")

    return result.stdout


def list_devices() -> list[str]:
    """
    列出所有已连接的 Android 设备 ID。

    Returns:
        设备序列号列表，空列表表示无设备连接。
    """
    try:
        output = _run_adb(["devices"]).decode("utf-8", errors="replace")
        lines = output.strip().splitlines()
        devices = []
        for line in lines[1:]:  # 跳过 "List of devices attached" 标题行
            line = line.strip()
            if line and "\t" in line:
                serial, status = line.split("\t", 1)
                if status.strip() == "device":
                    devices.append(serial.strip())
        return devices
    except RuntimeError:
        return []


def capture(region: list | None = None, device_id: str | None = None) -> bytes:
    """
    截取 Android 设备屏幕，返回 PNG bytes。

    Args:
        region:    [left, top, width, height] 截取区域，None 表示全屏。
        device_id: ADB 设备序列号，None 使用默认设备。

    Returns:
        PNG 格式图像的 bytes。

    Raises:
        RuntimeError: 截图失败或无设备连接。
    """
    # adb shell screencap -p 输出 PNG bytes 到 stdout
    png_bytes = _run_adb(["shell", "screencap", "-p"], device_id=device_id, timeout=15)

    # Windows 上 adb 会把 \n 转为 \r\n，需要修复
    png_bytes = png_bytes.replace(b"\r\n", b"\n")

    if region is not None:
        # 裁剪区域
        from PIL import Image
        assert len(region) == 4, "region 格式须为 [left, top, width, height]"
        img = Image.open(io.BytesIO(png_bytes))
        left, top, width, height = region
        cropped = img.crop((left, top, left + width, top + height))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    logger.debug("Android 截图完成 | device={} region={} size={}B", device_id or "default", region, len(png_bytes))
    return png_bytes


def capture_window(title: str, device_id: str | None = None) -> bytes:
    """
    Android 不支持按窗口标题截图，降级为全屏截图。

    Args:
        title:     窗口标题（忽略，Android 无法按标题截窗口）
        device_id: ADB 设备序列号

    Returns:
        全屏 PNG bytes。
    """
    logger.warning("Android 不支持窗口截图，降级为全屏 | title={}", title)
    return capture(device_id=device_id)
