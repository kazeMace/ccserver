"""
_platform/window_windows — Windows 窗口信息查询适配（不截图）。

提供两类查询：
1. list_windows()   — 列出所有可见窗口（title/proc/hwnd/bounds/center）
2. get_window_info()— 查询单个窗口的详细几何信息

依赖：
    pip install pywin32 psutil
    （pywin32 提供 win32gui/win32process，psutil 提供进程名查询）

注意：
    Windows 上没有 bundle_id 概念，app_name 匹配进程名（.exe 名称）。
    所有坐标均为物理像素坐标（已自动处理 DPI 感知）。
"""

from loguru import logger


def _check_deps():
    """
    检查 pywin32 和 psutil 是否安装。

    Returns:
        (win32gui 模块, psutil 模块)

    Raises:
        RuntimeError: 依赖未安装。
    """
    try:
        import win32gui
        import win32process
        import psutil
        return win32gui, win32process, psutil
    except ImportError:
        raise RuntimeError(
            "窗口查询依赖 pywin32 或 psutil 未安装，请运行："
            "conda run -n ccserver pip install pywin32 psutil"
        )


def _enum_visible_windows() -> list[tuple[int, str, str, tuple]]:
    """
    枚举所有可见且有标题的窗口。

    Returns:
        列表，每条为 (hwnd, window_title, proc_name, rect)
        rect 格式：(left, top, right, bottom)
    """
    win32gui, win32process, psutil = _check_deps()

    results = []

    def enum_callback(hwnd, _):
        # 只处理可见且有标题的窗口
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True

        # 通过 hwnd 获取 PID，再获取进程名
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            proc_name = proc.name()
        except Exception:
            proc_name = ""

        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        area = (right - left) * (bottom - top)
        if area > 0:
            results.append((hwnd, title, proc_name, rect))
        return True

    win32gui.EnumWindows(enum_callback, None)
    return results


def list_windows() -> list[dict]:
    """
    列出 Windows 上所有可见的普通窗口。

    每条窗口信息包含：
        title:    窗口标题
        proc:     进程名（.exe 文件名）
        hwnd:     窗口句柄（整数）
        bounds:   窗口位置和尺寸 {x, y, width, height}（物理像素）
        center:   窗口中心坐标 [x, y]

    Returns:
        窗口信息字典列表，按进程名排序。

    Raises:
        RuntimeError: 依赖未安装。
    """
    windows = _enum_visible_windows()

    results = []
    for hwnd, title, proc_name, rect in windows:
        left, top, right, bottom = rect
        w = right - left
        h = bottom - top
        cx = left + w // 2
        cy = top + h // 2
        results.append({
            "title":  title,
            "proc":   proc_name,
            "hwnd":   hwnd,
            "bounds": {"x": left, "y": top, "width": w, "height": h},
            "center": [cx, cy],
        })

    results.sort(key=lambda item: (item["proc"].lower(), item["title"].lower()))
    logger.info("Windows list_windows | count={}", len(results))
    return results


def get_window_info(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """
    查询 Windows 窗口的详细几何信息（不截图）。

    Args:
        app_name:     按进程名匹配（大小写不敏感，部分匹配）
        window_title: 按窗口标题匹配（大小写不敏感，部分匹配）
        bundle_id:    Windows 无此概念，传入时忽略（记日志提示）

    Returns:
        包含以下字段的字典：
            window.title/proc/hwnd/bounds/center/is_foreground
            monitors: 所有显示器列表

        失败时返回 {"error": "原因描述"}。

    Raises:
        RuntimeError: 依赖未安装。
    """
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id 查询，忽略 bundle_id={}", bundle_id)

    if not app_name and not window_title:
        return {"error": "需要至少提供 app_name 或 window_title"}

    win32gui, _, _ = _check_deps()

    windows = _enum_visible_windows()

    # 打分匹配：app_name 匹配进程名得 2 分，window_title 匹配标题得 1 分
    candidates = []
    for hwnd, title, proc_name, rect in windows:
        score = 0
        if app_name and app_name.lower() in proc_name.lower():
            score += 2
        if window_title and window_title.lower() in title.lower():
            score += 1

        if score > 0:
            left, top, right, bottom = rect
            area = (right - left) * (bottom - top)
            candidates.append((score, area, hwnd, title, proc_name, rect))

    if not candidates:
        return {
            "error": (
                f"未找到匹配窗口 ("
                f"app_name={app_name!r}, "
                f"window_title={window_title!r})"
            )
        }

    # 按得分降序 → 面积降序，取主窗口
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, hwnd, title, proc_name, rect = candidates[0]

    left, top, right, bottom = rect
    w = right - left
    h = bottom - top
    cx = left + w // 2
    cy = top + h // 2

    # 判断是否前台
    foreground_hwnd = win32gui.GetForegroundWindow()
    is_foreground = (hwnd == foreground_hwnd)
    foreground_title = win32gui.GetWindowText(foreground_hwnd) if not is_foreground else None

    # 获取显示器列表
    monitors = _get_monitors()

    on_monitor, crossed_monitors = _calc_monitor_placement(left, top, w, h, monitors)

    logger.info(
        "Windows get_window_info | title={!r} proc={} foreground={}",
        title, proc_name, is_foreground
    )

    return {
        "window": {
            "title":        title,
            "proc":         proc_name,
            "hwnd":         hwnd,
            "is_foreground": is_foreground,
            "foreground_app": foreground_title,
            "bounds": {
                "x":           left,
                "y":           top,
                "width":       w,
                "height":      h,
                "top_left":    [left, top],
                "top_right":   [left + w, top],
                "bottom_left": [left, top + h],
                "bottom_right":[left + w, top + h],
                "center":      [cx, cy],
            },
            "center": [cx, cy],
        },
        "monitors":      monitors,
        "monitor_count": len(monitors),
        "placement": {
            "on_monitor":      on_monitor,
            "monitors_spanned": crossed_monitors,
            "spanned_count":   len(crossed_monitors),
        },
    }


# ── 内部辅助函数 ────────────────────────────────────────────────────────────────


def _get_monitors() -> list[dict]:
    """
    获取所有显示器的几何信息（物理像素坐标）。

    Returns:
        显示器信息列表，每条包含 index / bounds / is_primary。
    """
    try:
        import mss
        with mss.mss() as sct:
            result = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue  # monitors[0] 是所有显示器合并
                result.append({
                    "index": i,
                    "bounds": {
                        "x":      mon["left"],
                        "y":      mon["top"],
                        "width":  mon["width"],
                        "height": mon["height"],
                    },
                    "is_primary": (i == 1),
                })
            return result
    except ImportError:
        return [{"index": 1, "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}, "is_primary": True}]


def _calc_monitor_placement(
    win_x: int, win_y: int, win_w: int, win_h: int,
    monitors: list[dict],
) -> tuple[int | None, list[int]]:
    """
    计算窗口的主显示器和跨屏情况（逻辑与 window_macos.py 一致）。

    Args:
        win_x, win_y: 窗口左上角坐标
        win_w, win_h: 窗口宽高
        monitors:     显示器列表

    Returns:
        (on_monitor: 主显示器编号 or None, crossed_monitors: 跨越的显示器编号列表)
    """
    cx = win_x + win_w // 2
    cy = win_y + win_h // 2

    on_monitor = None
    for mon in monitors:
        mb = mon["bounds"]
        if (mb["x"] <= cx <= mb["x"] + mb["width"] and
                mb["y"] <= cy <= mb["y"] + mb["height"]):
            on_monitor = mon["index"]
            break

    if on_monitor is None:
        for mon in monitors:
            mb = mon["bounds"]
            if (mb["x"] <= win_x < mb["x"] + mb["width"] and
                    mb["y"] <= win_y < mb["y"] + mb["height"]):
                on_monitor = mon["index"]
                break

    crossed = []
    win_right = win_x + win_w
    win_bottom = win_y + win_h
    for mon in monitors:
        mb = mon["bounds"]
        overlap_w = max(0, min(win_right, mb["x"] + mb["width"]) - max(win_x, mb["x"]))
        overlap_h = max(0, min(win_bottom, mb["y"] + mb["height"]) - max(win_y, mb["y"]))
        if overlap_w > 0 and overlap_h > 0:
            crossed.append(mon["index"])

    return on_monitor, crossed
