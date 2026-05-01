"""
_platform/apps_macos — macOS 运行中进程查询 + 窗口控制适配。

提供：
1. get_running_apps(filter, with_windows_only) — 获取运行中的进程列表
2. focus_window(...)    — 将窗口激活到前台
3. move_window(...)     — 移动窗口到指定桌面坐标
4. resize_window(...)   — 调整窗口大小
5. move_resize_window(...)  — 同时移动+调整大小
6. minimize_window(...) — 最小化窗口
7. maximize_window(...) — 最大化窗口
8. close_window(...)    — 关闭窗口

依赖：
    pip install pyobjc-framework-AppKit pyobjc-framework-Quartz mss
    AppKit 提供 NSRunningApplication（进程列表、激活）
    Quartz 提供窗口几何信息（定位目标窗口）
    mss 用于获取显示器信息
"""

from loguru import logger


# ── 进程列表 ──────────────────────────────────────────────────────────────────


def get_running_apps(
    filter_name: str | None = None,
    with_windows_only: bool = False,
) -> list[dict]:
    """
    获取 macOS 上所有运行中的进程。

    使用 NSWorkspace.runningApplications() 获取进程列表，
    可选按进程名过滤，可选只返回有可见窗口的进程。

    Args:
        filter_name:       按进程名过滤（大小写不敏感，部分匹配）。None 返回全部。
        with_windows_only: True = 只返回在 WindowList 中可见的进程。

    Returns:
        进程字典列表，每条包含：
            name:       本地化应用名
            bundle_id:  Bundle ID（系统进程可能为空）
            pid:        进程 ID
            has_window: 是否在 Quartz 窗口列表中有可见窗口

    Raises:
        RuntimeError: AppKit 未安装。
    """
    try:
        from AppKit import NSWorkspace
    except ImportError:
        raise RuntimeError(
            "AppKit 未安装，请运行：conda run -n ccserver pip install pyobjc-framework-AppKit"
        )

    # 获取所有运行中的应用（NSRunningApplication 列表）
    apps = NSWorkspace.sharedWorkspace().runningApplications()

    # 获取 Quartz 窗口列表，用于判断 has_window
    window_pids = _get_window_pids()

    results = []
    for app in apps:
        name = app.localizedName() or ""
        bundle_id = app.bundleIdentifier() or ""
        pid = app.processIdentifier()
        has_window = pid in window_pids

        # 按 filter_name 过滤
        if filter_name:
            if filter_name.lower() not in name.lower() and filter_name.lower() not in bundle_id.lower():
                continue

        # 按 with_windows_only 过滤
        if with_windows_only and not has_window:
            continue

        results.append({
            "name":       name,
            "bundle_id":  bundle_id,
            "pid":        pid,
            "has_window": has_window,
        })

    # 按名称排序
    results.sort(key=lambda x: x["name"].lower())
    logger.info("macOS get_running_apps | total={} filter={!r} windows_only={}", len(results), filter_name, with_windows_only)
    return results


def _get_window_pids() -> set[int]:
    """
    获取所有在 Quartz 窗口列表中有可见窗口的进程 PID 集合。

    Returns:
        PID 整数集合。
    """
    try:
        import Quartz.CoreGraphics as CG
        window_list = CG.CGWindowListCopyWindowInfo(
            CG.kCGWindowListOptionOnScreenOnly | CG.kCGWindowListExcludeDesktopElements,
            CG.kCGNullWindowID,
        )
        pids = set()
        for win in window_list:
            bounds = win.get("kCGWindowBounds", {})
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))
            layer = int(win.get("kCGWindowLayer", 0))
            if w > 0 and h > 0 and layer == 0:
                pid = win.get("kCGWindowOwnerPID")
                if pid:
                    pids.add(int(pid))
        return pids
    except Exception:
        return set()


# ── 窗口控制 ──────────────────────────────────────────────────────────────────


def _find_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict | None:
    """
    按 app_name / window_title / bundle_id 定位目标窗口，返回窗口信息字典。

    复用 window_macos.get_window_info 的匹配逻辑。

    Args:
        app_name:     按进程名匹配
        window_title: 按窗口标题匹配
        bundle_id:    按 Bundle ID 精确匹配（最高优先级）

    Returns:
        包含 window_id / pid / bundle_id / bounds / owner / title 的字典，
        找不到时返回 None。
    """
    from .window_macos import get_window_info
    info = get_window_info(app_name=app_name, window_title=window_title, bundle_id=bundle_id)
    if "error" in info:
        return None
    return info.get("window")


def focus_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """
    将指定窗口激活到前台（macOS）。

    优先通过 NSRunningApplication.activateWithOptions_ 激活整个应用，
    再通过 AppleScript 将特定窗口提到最前。

    Args:
        app_name:     按进程名定位
        window_title: 按窗口标题定位
        bundle_id:    按 Bundle ID 定位（最精确）

    Returns:
        {"ok": True, "owner": ..., "title": ...} 成功
        {"error": "原因"} 失败
    """
    win = _find_window(app_name, window_title, bundle_id)
    if win is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r}, bundle_id={bundle_id!r})"}

    pid = win.get("pid")
    owner = win.get("owner", "")
    title = win.get("title", "")
    win_bundle_id = win.get("bundle_id") or bundle_id

    try:
        from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
        if win_bundle_id:
            apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(win_bundle_id)
            if apps and len(apps) > 0:
                apps[0].activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                logger.info("macOS focus_window | bundle_id={} owner={}", win_bundle_id, owner)
                return {"ok": True, "owner": owner, "title": title}

        if pid:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app:
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                logger.info("macOS focus_window | pid={} owner={}", pid, owner)
                return {"ok": True, "owner": owner, "title": title}

    except ImportError:
        pass
    except Exception as e:
        logger.warning("AppKit 激活失败，尝试 AppleScript: {}", e)

    # 降级：AppleScript
    import subprocess
    script = f'tell application "{owner}" to activate'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    if result.returncode == 0:
        logger.info("macOS focus_window (osascript) | owner={}", owner)
        return {"ok": True, "owner": owner, "title": title}

    return {"error": f"激活窗口失败：{result.stderr.decode().strip()}"}


def move_window(
    x: int,
    y: int,
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """
    将窗口左上角移动到桌面坐标 (x, y)（macOS）。

    使用 AppleScript 设置窗口位置（兼容性最好）。
    先获取当前窗口大小，保持大小不变只移动位置。

    Args:
        x:            目标 X 坐标（桌面像素）
        y:            目标 Y 坐标（桌面像素）
        app_name:     按进程名定位
        window_title: 按窗口标题定位
        bundle_id:    按 Bundle ID 定位

    Returns:
        {"ok": True, "owner": ..., "from": [ox,oy], "to": [x,y]} 成功
        {"error": "原因"} 失败
    """
    win = _find_window(app_name, window_title, bundle_id)
    if win is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r}, bundle_id={bundle_id!r})"}

    owner = win.get("owner", "")
    bounds = win.get("bounds", {})
    old_x = bounds.get("x", 0)
    old_y = bounds.get("y", 0)

    # 用 AppleScript 移动窗口
    import subprocess
    script = f'tell application "{owner}" to set position of front window to {{{x}, {y}}}'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    if result.returncode == 0:
        logger.info("macOS move_window | owner={} ({},{}) → ({},{})", owner, old_x, old_y, x, y)
        return {"ok": True, "owner": owner, "title": win.get("title", ""), "from": [old_x, old_y], "to": [x, y]}

    return {"error": f"移动窗口失败：{result.stderr.decode().strip()}"}


def resize_window(
    width: int,
    height: int,
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """
    调整窗口大小（macOS）。

    保持窗口位置不变，只改变宽高。

    Args:
        width:        目标宽度（像素）
        height:       目标高度（像素）
        app_name/window_title/bundle_id: 窗口定位参数

    Returns:
        {"ok": True, "owner": ..., "from": [ow,oh], "to": [w,h]} 成功
        {"error": "原因"} 失败
    """
    win = _find_window(app_name, window_title, bundle_id)
    if win is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r}, bundle_id={bundle_id!r})"}

    owner = win.get("owner", "")
    bounds = win.get("bounds", {})
    old_w = bounds.get("width", 0)
    old_h = bounds.get("height", 0)

    import subprocess
    script = f'tell application "{owner}" to set size of front window to {{{width}, {height}}}'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    if result.returncode == 0:
        logger.info("macOS resize_window | owner={} {}x{} → {}x{}", owner, old_w, old_h, width, height)
        return {"ok": True, "owner": owner, "title": win.get("title", ""), "from": [old_w, old_h], "to": [width, height]}

    return {"error": f"调整窗口大小失败：{result.stderr.decode().strip()}"}


def move_resize_window(
    x: int,
    y: int,
    width: int,
    height: int,
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """
    同时移动窗口位置和调整大小（macOS）。

    Args:
        x/y:          目标左上角坐标
        width/height: 目标宽高
        app_name/window_title/bundle_id: 窗口定位参数

    Returns:
        {"ok": True, ...} 成功，{"error": "..."} 失败
    """
    win = _find_window(app_name, window_title, bundle_id)
    if win is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r}, bundle_id={bundle_id!r})"}

    owner = win.get("owner", "")
    bounds = win.get("bounds", {})
    old_x, old_y = bounds.get("x", 0), bounds.get("y", 0)
    old_w, old_h = bounds.get("width", 0), bounds.get("height", 0)

    import subprocess
    # AppleScript 设置 bounds：{left, top, right, bottom}
    right = x + width
    bottom = y + height
    script = f'tell application "{owner}" to set bounds of front window to {{{x}, {y}, {right}, {bottom}}}'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    if result.returncode == 0:
        logger.info("macOS move_resize_window | owner={} ({},{} {}x{}) → ({},{} {}x{})",
                    owner, old_x, old_y, old_w, old_h, x, y, width, height)
        return {
            "ok": True,
            "owner": owner,
            "title": win.get("title", ""),
            "from": {"x": old_x, "y": old_y, "width": old_w, "height": old_h},
            "to":   {"x": x, "y": y, "width": width, "height": height},
        }

    return {"error": f"移动+调整窗口失败：{result.stderr.decode().strip()}"}


def minimize_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """最小化窗口（macOS）。"""
    win = _find_window(app_name, window_title, bundle_id)
    if win is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r}, bundle_id={bundle_id!r})"}

    owner = win.get("owner", "")
    import subprocess
    script = f'tell application "{owner}" to set miniaturized of front window to true'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    if result.returncode == 0:
        logger.info("macOS minimize_window | owner={}", owner)
        return {"ok": True, "owner": owner, "title": win.get("title", "")}
    return {"error": f"最小化失败：{result.stderr.decode().strip()}"}


def maximize_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """最大化窗口（macOS，使用 zoom 命令）。"""
    win = _find_window(app_name, window_title, bundle_id)
    if win is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r}, bundle_id={bundle_id!r})"}

    owner = win.get("owner", "")
    import subprocess
    script = f'tell application "{owner}" to zoom front window'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    if result.returncode == 0:
        logger.info("macOS maximize_window | owner={}", owner)
        return {"ok": True, "owner": owner, "title": win.get("title", "")}
    return {"error": f"最大化失败：{result.stderr.decode().strip()}"}


def close_window(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """关闭窗口（macOS）。"""
    win = _find_window(app_name, window_title, bundle_id)
    if win is None:
        return {"error": f"未找到匹配窗口 (app_name={app_name!r}, window_title={window_title!r}, bundle_id={bundle_id!r})"}

    owner = win.get("owner", "")
    import subprocess
    script = f'tell application "{owner}" to close front window'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    if result.returncode == 0:
        logger.info("macOS close_window | owner={}", owner)
        return {"ok": True, "owner": owner, "title": win.get("title", "")}
    return {"error": f"关闭窗口失败：{result.stderr.decode().strip()}"}
