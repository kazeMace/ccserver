"""
_platform/input_windows — Windows 鼠标键盘控制适配。

优先使用 pyautogui（跨平台接口统一），
可选 win32api（更底层，处理 DPI 坐标偏差）。

依赖：
    pip install pyautogui pillow
    # 可选：pip install pywin32（精确 DPI 坐标处理）
"""

import time
from loguru import logger


def _check_deps():
    """检查 pyautogui 是否已安装。"""
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        raise RuntimeError(
            "鼠标键盘控制依赖 pyautogui 未安装，请运行："
            "conda run -n ccserver pip install pyautogui pillow"
        )


def click(x: int, y: int, button: str = "left", clicks: int = 1) -> None:
    """
    在 Windows 上执行鼠标点击。

    Args:
        x:       点击 X 坐标
        y:       点击 Y 坐标
        button:  "left" | "right" | "middle"
        clicks:  点击次数
    """
    pyautogui = _check_deps()
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    logger.debug("Windows 鼠标点击 | x={} y={} button={} clicks={}", x, y, button, clicks)
    pyautogui.click(x=x, y=y, button=button, clicks=clicks, interval=0.1)


def move_to(x: int, y: int, duration: float = 0.2) -> None:
    """移动鼠标（不点击）。"""
    pyautogui = _check_deps()
    logger.debug("Windows 鼠标移动 | x={} y={} duration={}", x, y, duration)
    pyautogui.moveTo(x, y, duration=duration)


def drag_to(x: int, y: int, duration: float = 0.3) -> None:
    """拖拽鼠标到指定坐标。"""
    pyautogui = _check_deps()
    logger.debug("Windows 鼠标拖拽 | x={} y={} duration={}", x, y, duration)
    pyautogui.dragTo(x, y, duration=duration, button="left")


def scroll(x: int, y: int, delta: int) -> None:
    """滚动鼠标滚轮。"""
    pyautogui = _check_deps()
    logger.debug("Windows 滚轮滚动 | x={} y={} delta={}", x, y, delta)
    pyautogui.scroll(delta, x=x, y=y)


def type_text(text: str, method: str = "paste") -> None:
    """
    在当前焦点处输入文字。

    Args:
        text:   要输入的文字
        method: "paste" = 剪贴板粘贴（推荐，支持中文）
                "type"  = 逐字符输入（仅 ASCII）
    """
    pyautogui = _check_deps()
    logger.debug("Windows 文字输入 | method={} text={!r}", method, text[:20])

    if method == "paste":
        try:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
        except ImportError:
            logger.warning("pyperclip 未安装，降级为逐字输入")
            pyautogui.write(text, interval=0.03)
    else:
        pyautogui.write(text, interval=0.03)


def press_key(key: str) -> None:
    """
    按下快捷键或单个按键。

    Args:
        key: 键名，支持单键或组合键（用 + 连接），如 'ctrl+c', 'enter'。
    """
    pyautogui = _check_deps()
    logger.debug("Windows 按键 | key={}", key)

    if "+" in key:
        parts = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*parts)
    else:
        pyautogui.press(key)
