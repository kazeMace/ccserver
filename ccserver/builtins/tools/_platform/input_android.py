"""
_platform/input_android — Android 设备输入控制适配（通过 ADB）。

通过 adb shell input 命令模拟触摸和键盘输入。
设备需要开启 USB 调试模式。

依赖：
    adb 命令行工具（需在 PATH 中）
"""

import subprocess
import time
from loguru import logger


def _adb(args: list[str], device_id: str | None = None, timeout: int = 10) -> str:
    """
    运行 adb 命令，返回 stdout 字符串。

    Args:
        args:      adb 子命令参数列表
        device_id: 设备序列号
        timeout:   超时秒数

    Returns:
        命令输出字符串。

    Raises:
        RuntimeError: adb 未安装或命令失败。
    """
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += args

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(
            "adb 命令未找到，请安装 Android SDK Platform-Tools 并将 adb 加入 PATH"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"adb 命令超时（{timeout}s）")

    if result.returncode != 0:
        err = result.stderr.strip()
        raise RuntimeError(f"adb 失败 (exit={result.returncode}): {err}")

    return result.stdout.strip()


def click(x: int, y: int, button: str = "left", clicks: int = 1, device_id: str | None = None) -> None:
    """
    在 Android 设备上模拟触摸点击。

    Args:
        x:         点击 X 坐标（设备像素）
        y:         点击 Y 坐标（设备像素）
        button:    忽略（Android 无右键/中键区分）
        clicks:    点击次数（>1 时多次 tap）
        device_id: ADB 设备序列号
    """
    logger.debug("Android tap | x={} y={} clicks={} device={}", x, y, clicks, device_id or "default")
    for _ in range(clicks):
        _adb(["shell", "input", "tap", str(x), str(y)], device_id=device_id)
        if clicks > 1:
            time.sleep(0.1)


def move_to(x: int, y: int, duration: float = 0.2, device_id: str | None = None) -> None:
    """
    Android 不支持单纯移动鼠标，此操作为空实现。
    如需模拟滑动，使用 drag_to。
    """
    logger.debug("Android move_to（无操作）| x={} y={}", x, y)


def drag_to(x: int, y: int, duration: float = 0.3, start_x: int = 0, start_y: int = 0, device_id: str | None = None) -> None:
    """
    在 Android 上模拟滑动（swipe）。

    Args:
        x:         目标 X 坐标
        y:         目标 Y 坐标
        duration:  滑动时长（秒），转换为毫秒传给 adb
        start_x:   起始 X 坐标
        start_y:   起始 Y 坐标
        device_id: ADB 设备序列号
    """
    duration_ms = int(duration * 1000)
    logger.debug("Android swipe | ({},{}) -> ({},{}) {}ms device={}", start_x, start_y, x, y, duration_ms, device_id or "default")
    _adb(["shell", "input", "swipe", str(start_x), str(start_y), str(x), str(y), str(duration_ms)], device_id=device_id)


def scroll(x: int, y: int, delta: int, device_id: str | None = None) -> None:
    """
    在 Android 上模拟滚动（通过 swipe 实现）。

    Args:
        x:         滚动中心 X
        y:         滚动中心 Y
        delta:     滚动量（正数向上，负数向下）
        device_id: ADB 设备序列号
    """
    swipe_distance = delta * 100  # 每单位对应 100 像素
    end_y = y - swipe_distance
    logger.debug("Android scroll | x={} y={} delta={} device={}", x, y, delta, device_id or "default")
    _adb(["shell", "input", "swipe", str(x), str(y), str(x), str(end_y), "300"], device_id=device_id)


def type_text(text: str, method: str = "paste", device_id: str | None = None) -> None:
    """
    在 Android 当前焦点输入框中输入文字。

    注意：adb input text 对中文支持有限，建议先切换输入法为 ADBKeyboard。

    Args:
        text:      要输入的文字（建议 ASCII，中文支持有限）
        method:    忽略（Android 只有一种方式）
        device_id: ADB 设备序列号
    """
    logger.debug("Android type | text={!r} device={}", text[:20], device_id or "default")
    # adb input text 需要对空格和特殊字符转义
    escaped = text.replace(" ", "%s").replace("'", "\\'").replace("&", "\\&")
    _adb(["shell", "input", "text", escaped], device_id=device_id)


def press_key(key: str, device_id: str | None = None) -> None:
    """
    按下 Android 按键（keyevent）。

    Args:
        key:       按键名，支持：
                   - Android keycode：'KEYCODE_BACK', 'KEYCODE_HOME', 'KEYCODE_ENTER'
                   - 通用别名：'back', 'home', 'enter', 'escape'
        device_id: ADB 设备序列号
    """
    # 通用别名映射
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
    logger.debug("Android keyevent | key={} → {} device={}", key, keycode, device_id or "default")
    _adb(["shell", "input", "keyevent", keycode], device_id=device_id)
