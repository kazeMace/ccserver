"""
_platform/input_macos — macOS 鼠标键盘控制适配。

使用 pyautogui（跨平台），需要 macOS Accessibility 权限。
首次运行时 macOS 会弹出权限申请对话框。

依赖：
    pip install pyautogui pillow
    # macOS 需要在 系统设置 → 隐私与安全性 → 辅助功能 中授权运行程序
"""

import time
from loguru import logger


def _check_deps():
    """检查 pyautogui 是否已安装，未安装时给出明确提示。"""
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
    在 macOS 上执行鼠标点击。

    Args:
        x:       点击 X 坐标（逻辑坐标）
        y:       点击 Y 坐标（逻辑坐标）
        button:  "left" | "right" | "middle"
        clicks:  点击次数（1=单击，2=双击）
    """
    pyautogui = _check_deps()
    # 安全限制：防止意外操作（可根据需要调整）
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05  # 每次操作后短暂停顿，避免过快触发失败

    logger.debug("macOS 鼠标点击 | x={} y={} button={} clicks={}", x, y, button, clicks)
    pyautogui.click(x=x, y=y, button=button, clicks=clicks, interval=0.1)


def move_to(x: int, y: int, duration: float = 0.2) -> None:
    """
    移动鼠标到指定坐标（不点击）。

    Args:
        x:        目标 X 坐标
        y:        目标 Y 坐标
        duration: 移动动画时长（秒），0 表示瞬间移动
    """
    pyautogui = _check_deps()
    logger.debug("macOS 鼠标移动 | x={} y={} duration={}", x, y, duration)
    pyautogui.moveTo(x, y, duration=duration)


def drag_to(x: int, y: int, duration: float = 0.3) -> None:
    """
    拖拽鼠标到指定坐标（按住左键移动）。

    Args:
        x:        目标 X 坐标
        y:        目标 Y 坐标
        duration: 拖拽时长（秒）
    """
    pyautogui = _check_deps()
    logger.debug("macOS 鼠标拖拽 | x={} y={} duration={}", x, y, duration)
    pyautogui.dragTo(x, y, duration=duration, button="left")


def scroll(x: int, y: int, delta: int) -> None:
    """
    在指定位置滚动鼠标滚轮。

    Args:
        x:     滚动位置 X
        y:     滚动位置 Y
        delta: 滚动量（正数向上，负数向下）
    """
    pyautogui = _check_deps()
    logger.debug("macOS 滚轮滚动 | x={} y={} delta={}", x, y, delta)
    pyautogui.scroll(delta, x=x, y=y)


def type_text(text: str, method: str = "paste") -> None:
    """
    在当前焦点处输入文字。

    Args:
        text:   要输入的文字
        method: "paste" = 剪贴板粘贴（推荐，速度快，支持中文）
                "type"  = 逐字符输入（适合英文，速度慢但更真实）
    """
    pyautogui = _check_deps()
    logger.debug("macOS 文字输入 | method={} text={!r}", method, text[:20])

    if method == "paste":
        # 剪贴板粘贴：速度快，支持 Unicode / 中文
        try:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("command", "v")
            time.sleep(0.1)  # 等待粘贴完成
        except ImportError:
            logger.warning("pyperclip 未安装，降级为逐字输入")
            pyautogui.write(text, interval=0.03)
    else:
        # 逐字符输入（仅支持 ASCII）
        pyautogui.write(text, interval=0.03)


def press_key(key: str) -> None:
    """
    按下快捷键或单个按键。

    Args:
        key: 键名，支持：
             - 单键：'enter', 'escape', 'tab', 'backspace', 'delete'
             - 组合键：'command+c', 'command+v', 'ctrl+z' 等（用 + 连接）
    """
    pyautogui = _check_deps()
    logger.debug("macOS 按键 | key={}", key)

    if "+" in key:
        # 组合键：'command+c' → hotkey('command', 'c')
        parts = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*parts)
    else:
        pyautogui.press(key)
