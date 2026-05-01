"""
_platform/apps_windows — Windows 运行中进程查询 + 窗口控制适配。

提供：
1. get_running_apps(filter, with_windows_only) — 获取运行中的进程列表
2. focus_window(...)    — 将窗口激活到前台
3. move_window(...)     — 移动窗口
4. resize_window(...)   — 调整大小
5. move_resize_window(...)  — 同时移动+调整大小
6. minimize_window(...) — 最小化
7. maximize_window(...) — 最大化
8. close_window(...)    — 关闭

依赖：
    pip install pywin32 psutil
    pywin32 提供 win32gui/win32process/win32con
    psutil 提供进程列表查询
"""

from loguru import logger


def _check_deps():
    """检查 pywin32 和 psutil 是否安装。"""
    try:
        import win32gui
        import win32process
        import win32con
        import psutil
        return win32gui, win32process, win32con, psutil
    except ImportError:
        raise RuntimeError(
            "依赖 pywin32 或 psutil 未安装，请运行：conda run -n ccserver pip install pywin32 psutil"
        )


# ── 进程列表 ──────────────────────────────────────────────────────────────────


def get_running_apps(
    filter_name: str | None = None,
    with_windows_only: bool = False,
) -> list[dict]:
    """
    获取 Windows 上所有运行中的进程。

    使用 psutil.process_iter() 获取进程列表，
    通过 win32gui 判断哪些进程有可见窗口。

    Args:
        filter_name:       按进程名过滤（大小写不敏感，部分匹配）。None 返回全部。
        with_windows_only: True = 只返回有可见窗口的进程。

    Returns:
        进程字典列表，每条包含：
            name:       进程名（.exe）
            bundle_id:  Windows 无此概念，固定为空字符串
            pid:        进程 ID
            has_window: 是否有可见窗口

    Raises:
        RuntimeError: 依赖未安装。
    """
    _, _, _, psutil = _check_deps()

    # 获取有窗口的 PID 集合
    window_pids = _get_window_pids()

    results = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"] or ""
            pid = proc.info["pid"]
            has_window = pid in window_pids

            if filter_name and filter_name.lower() not in name.lower():
                continue
            if with_windows_only and not has_window:
                continue

            results.append({
                "name":       name,
                "bundle_id":  "",   # Windows 无 bundle_id 概念
                "pid":        pid,
                "has_window": has_window,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    results.sort(key=lambda x: x["name"].lower())
    logger.info("Windows get_running_apps | total={} filter={!r} windows_only={}", len(results), filter_name, with_windows_only)
    return results


def _get_window_pids() -> set[int]:
    """获取所有有可见窗口的进程 PID 集合。"""
    try:
        win32gui, win32process, _, _ = _check_deps()
        pids = set()

        def callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    pids.add(pid)
                except Exception:
                    pass
            return True

        win32gui.EnumWindows(callback, None)
        return pids
    except Exception:
        return set()


# ── 窗口定位 ──────────────────────────────────────────────────────────────────


def _find_hwnd(
    app_name: str | None = None,
    window_title: str | None = None,
) -> tuple[int | None, str, str]:
    """
    定位目标窗口句柄（hwnd）。

    Args:
        app_name:     按进程名匹配
        window_title: 按窗口标题匹配

    Returns:
        (hwnd, proc_name, title)，找不到时 hwnd=None。
    """
    from .window_windows import _enum_visible_windows
    windows = _enum_visible_windows()

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
        return None, "", ""

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, hwnd, title, proc_name, _ = candidates[0]
    return hwnd, proc_name, title


# ── 窗口控制 ──────────────────────────────────────────────────────────────────


def focus_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """将指定窗口激活到前台（Windows）。"""
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id，忽略")

    win32gui, _, win32con, _ = _check_deps()
    hwnd, proc_name, title = _find_hwnd(app_name, window_title)
    if hwnd is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r})"}

    try:
        # 如果窗口最小化，先还原
        if win32gui.IsIconic(hwnd):
            import win32con as wc
            win32gui.ShowWindow(hwnd, wc.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        logger.info("Windows focus_window | hwnd={} proc={}", hwnd, proc_name)
        return {"ok": True, "owner": proc_name, "title": title}
    except Exception as e:
        return {"error": f"激活窗口失败：{e}"}


def move_window(
    x: int,
    y: int,
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """将窗口移动到桌面坐标 (x, y)，保持原有大小（Windows）。"""
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id，忽略")

    win32gui, _, _, _ = _check_deps()
    hwnd, proc_name, title = _find_hwnd(app_name, window_title)
    if hwnd is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r})"}

    try:
        rect = win32gui.GetWindowRect(hwnd)
        old_x, old_y = rect[0], rect[1]
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        logger.info("Windows move_window | hwnd={} ({},{}) → ({},{})", hwnd, old_x, old_y, x, y)
        return {"ok": True, "owner": proc_name, "title": title, "from": [old_x, old_y], "to": [x, y]}
    except Exception as e:
        return {"error": f"移动窗口失败：{e}"}


def resize_window(
    width: int,
    height: int,
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """调整窗口大小，保持位置不变（Windows）。"""
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id，忽略")

    win32gui, _, _, _ = _check_deps()
    hwnd, proc_name, title = _find_hwnd(app_name, window_title)
    if hwnd is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r})"}

    try:
        rect = win32gui.GetWindowRect(hwnd)
        x, y = rect[0], rect[1]
        old_w = rect[2] - rect[0]
        old_h = rect[3] - rect[1]
        win32gui.MoveWindow(hwnd, x, y, width, height, True)
        logger.info("Windows resize_window | hwnd={} {}x{} → {}x{}", hwnd, old_w, old_h, width, height)
        return {"ok": True, "owner": proc_name, "title": title, "from": [old_w, old_h], "to": [width, height]}
    except Exception as e:
        return {"error": f"调整窗口大小失败：{e}"}


def move_resize_window(
    x: int,
    y: int,
    width: int,
    height: int,
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """同时移动窗口位置和调整大小（Windows）。"""
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id，忽略")

    win32gui, _, _, _ = _check_deps()
    hwnd, proc_name, title = _find_hwnd(app_name, window_title)
    if hwnd is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r})"}

    try:
        rect = win32gui.GetWindowRect(hwnd)
        old_x, old_y = rect[0], rect[1]
        old_w, old_h = rect[2] - rect[0], rect[3] - rect[1]
        win32gui.MoveWindow(hwnd, x, y, width, height, True)
        logger.info("Windows move_resize_window | hwnd={} ({},{} {}x{}) → ({},{} {}x{})",
                    hwnd, old_x, old_y, old_w, old_h, x, y, width, height)
        return {
            "ok": True, "owner": proc_name, "title": title,
            "from": {"x": old_x, "y": old_y, "width": old_w, "height": old_h},
            "to":   {"x": x, "y": y, "width": width, "height": height},
        }
    except Exception as e:
        return {"error": f"移动+调整窗口失败：{e}"}


def minimize_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """最小化窗口（Windows）。"""
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id，忽略")

    win32gui, _, _, _ = _check_deps()
    hwnd, proc_name, title = _find_hwnd(app_name, window_title)
    if hwnd is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r})"}

    try:
        import win32con
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        logger.info("Windows minimize_window | hwnd={} proc={}", hwnd, proc_name)
        return {"ok": True, "owner": proc_name, "title": title}
    except Exception as e:
        return {"error": f"最小化失败：{e}"}


def maximize_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """最大化窗口（Windows）。"""
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id，忽略")

    win32gui, _, _, _ = _check_deps()
    hwnd, proc_name, title = _find_hwnd(app_name, window_title)
    if hwnd is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r})"}

    try:
        import win32con
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        logger.info("Windows maximize_window | hwnd={} proc={}", hwnd, proc_name)
        return {"ok": True, "owner": proc_name, "title": title}
    except Exception as e:
        return {"error": f"最大化失败：{e}"}


def close_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """关闭窗口（Windows）。"""
    if bundle_id:
        logger.warning("Windows 不支持 bundle_id，忽略")

    win32gui, _, win32con, _ = _check_deps()
    hwnd, proc_name, title = _find_hwnd(app_name, window_title)
    if hwnd is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r})"}

    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        logger.info("Windows close_window | hwnd={} proc={}", hwnd, proc_name)
        return {"ok": True, "owner": proc_name, "title": title}
    except Exception as e:
        return {"error": f"关闭窗口失败：{e}"}
