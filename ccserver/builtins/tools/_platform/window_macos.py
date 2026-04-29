"""
_platform/window_macos — macOS 窗口信息查询适配（不截图）。

提供两类查询：
1. list_windows()   — 列出所有可见窗口（owner/title/id/bounds/center）
2. get_window_info()— 查询单个窗口的详细几何信息（bounds/前台状态/所属显示器）

依赖：
    pip install pyobjc-framework-Quartz pyobjc-framework-AppKit mss
    （Quartz 是必须的，AppKit 可选，mss 用于获取显示器信息）

注意：
    所有坐标均为 macOS 逻辑坐标（points），非物理像素。
    Retina 屏幕上，逻辑坐标是物理像素的一半。
"""

from loguru import logger


# 菜单栏/状态栏等系统装饰窗口的过滤条件：高度 <= 40px 且宽度 > 2000px
_MENU_BAR_H_MAX = 40
_MENU_BAR_W_MIN = 2000


def _get_quartz_window_list():
    """
    获取 macOS 所有在屏可见窗口的信息列表。

    Returns:
        Quartz 窗口信息列表（原始 NSDictionary 列表）。

    Raises:
        RuntimeError: pyobjc-framework-Quartz 未安装。
    """
    try:
        import Quartz.CoreGraphics as CG
    except ImportError:
        raise RuntimeError(
            "窗口查询依赖 pyobjc-framework-Quartz 未安装，请运行："
            "conda run -n ccserver pip install pyobjc-framework-Quartz"
        )

    return CG.CGWindowListCopyWindowInfo(
        CG.kCGWindowListOptionOnScreenOnly | CG.kCGWindowListExcludeDesktopElements,
        CG.kCGNullWindowID,
    )


def _is_decoration_window(w: int, h: int, layer: int) -> bool:
    """
    判断是否为系统装饰窗口（菜单栏、Dock、状态栏等），应跳过这类窗口。

    Args:
        w:     窗口宽度（像素）
        h:     窗口高度（像素）
        layer: Quartz 窗口层级（0=普通窗口）

    Returns:
        True 表示应跳过，False 表示是普通窗口。
    """
    # 零尺寸窗口
    if w <= 0 or h <= 0:
        return True
    # 系统层级窗口（Dock、菜单等），layer=0 才是普通应用窗口
    if layer != 0:
        return True
    # 菜单栏特征：极矮且极宽
    if h <= _MENU_BAR_H_MAX and w > _MENU_BAR_W_MIN:
        return True
    return False


def _build_window_dict(win) -> dict:
    """
    从 Quartz 窗口信息构建统一格式的窗口字典。

    Args:
        win: Quartz CGWindowListCopyWindowInfo 返回的单条窗口信息。

    Returns:
        包含 owner/title/window_id/bounds/center 的字典。
    """
    bounds = win.get("kCGWindowBounds", {})
    x = int(bounds.get("X", 0))
    y = int(bounds.get("Y", 0))
    w = int(bounds.get("Width", 0))
    h = int(bounds.get("Height", 0))
    center_x = x + w // 2
    center_y = y + h // 2

    return {
        "owner":     win.get("kCGWindowOwnerName", "") or "",
        "title":     win.get("kCGWindowName", "") or "",
        "window_id": win.get("kCGWindowNumber"),
        "pid":       win.get("kCGWindowOwnerPID"),
        "layer":     win.get("kCGWindowLayer", 0),
        "bounds": {
            "x":      x,
            "y":      y,
            "width":  w,
            "height": h,
        },
        "center": [center_x, center_y],
    }


def list_windows() -> list[dict]:
    """
    列出 macOS 上所有可见的普通窗口（排除菜单栏、Dock 等系统装饰窗口）。

    返回的列表按 owner 名排序，每条包含：
        owner:     进程/应用名（对应 --app 参数）
        title:     窗口标题（对应 --window 参数，游戏窗口可能为空）
        window_id: Quartz 窗口编号
        pid:       进程 ID
        layer:     窗口层级（0 = 普通窗口）
        bounds:    窗口位置和尺寸 {x, y, width, height}（逻辑坐标）
        center:    窗口中心坐标 [x, y]

    Returns:
        窗口信息字典列表，空列表表示无可见窗口。

    Raises:
        RuntimeError: Quartz 未安装。
    """
    window_list = _get_quartz_window_list()

    results = []
    for win in window_list:
        bounds = win.get("kCGWindowBounds", {})
        w = int(bounds.get("Width", 0))
        h = int(bounds.get("Height", 0))
        layer = int(win.get("kCGWindowLayer", 0))

        # 跳过系统装饰窗口
        if _is_decoration_window(w, h, layer):
            continue

        results.append(_build_window_dict(win))

    # 按 owner 名排序，方便 Agent 阅读
    results.sort(key=lambda item: (item["owner"].lower(), item["title"].lower()))

    logger.info("macOS list_windows | count={}", len(results))
    return results


def get_window_info(
    app_name: str | None = None,
    window_title: str | None = None,
    bundle_id: str | None = None,
) -> dict:
    """
    查询匹配窗口的详细几何信息（不截图）。

    匹配优先级：bundle_id → app_name → window_title
    多个匹配窗口时按"得分×面积"降序取第一个（主窗口）。

    Args:
        app_name:     按进程/应用名匹配（kCGWindowOwnerName，大小写不敏感，部分匹配）
        window_title: 按窗口标题匹配（kCGWindowName，大小写不敏感，部分匹配）
        bundle_id:    按 macOS Bundle ID 精确匹配（最精确，先解析为 app_name）

    Returns:
        包含以下字段的字典：
            window.owner/title/window_id/pid/layer/bounds/center/is_foreground/foreground_app
            monitors:   所有显示器列表
            placement:  窗口所在显示器 + 跨屏情况

        失败时返回 {"error": "原因描述"}。

    Raises:
        RuntimeError: Quartz 未安装。
    """
    # 通过 bundle_id 解析为 app_name（最精确的入口）
    if bundle_id:
        app_name = _resolve_bundle_id(bundle_id) or app_name

    if not app_name and not window_title:
        return {"error": "需要至少提供 app_name、window_title、bundle_id 中的一个"}

    window_list = _get_quartz_window_list()

    # ── 匹配窗口（打分策略） ───────────────────────────────────────────────────
    # app_name 匹配 owner 得 2 分，window_title 匹配 title 得 1 分
    candidates = []
    for win in window_list:
        bounds = win.get("kCGWindowBounds", {})
        w = int(bounds.get("Width", 0))
        h = int(bounds.get("Height", 0))
        layer = int(win.get("kCGWindowLayer", 0))

        if _is_decoration_window(w, h, layer):
            continue

        win_owner = win.get("kCGWindowOwnerName", "") or ""
        win_title = win.get("kCGWindowName", "") or ""

        score = 0
        if app_name and app_name.lower() in win_owner.lower():
            score += 2
        if window_title and window_title.lower() in win_title.lower():
            score += 1

        if score > 0:
            candidates.append((score, w * h, win))

    # 兜底：window_title 无标题匹配时，尝试匹配 owner 名
    if not candidates and window_title and not app_name:
        for win in window_list:
            bounds = win.get("kCGWindowBounds", {})
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))
            layer = int(win.get("kCGWindowLayer", 0))
            if _is_decoration_window(w, h, layer):
                continue
            win_owner = win.get("kCGWindowOwnerName", "") or ""
            if window_title.lower() in win_owner.lower():
                candidates.append((1, w * h, win))

    if not candidates:
        return {
            "error": (
                f"未找到匹配窗口 ("
                f"app_name={app_name!r}, "
                f"window_title={window_title!r}, "
                f"bundle_id={bundle_id!r})"
            )
        }

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = candidates[0][2]

    # ── 构建基础信息 ──────────────────────────────────────────────────────────
    win_dict = _build_window_dict(best)
    win_pid = win_dict["pid"]
    win_x = win_dict["bounds"]["x"]
    win_y = win_dict["bounds"]["y"]
    win_w = win_dict["bounds"]["width"]
    win_h = win_dict["bounds"]["height"]
    win_cx, win_cy = win_dict["center"]

    # 补充 bundle_id（通过 pid → NSRunningApplication）
    win_bundle_id = _get_bundle_id_by_pid(win_pid) if win_pid else None
    win_dict["bundle_id"] = win_bundle_id

    # ── 判断是否前台 ───────────────────────────────────────────────────────────
    is_foreground, foreground_app = _check_is_foreground(win_pid, win_bundle_id)
    win_dict["is_foreground"] = is_foreground
    win_dict["foreground_app"] = foreground_app if not is_foreground else None

    # ── 补充 bounds 的四角坐标 ─────────────────────────────────────────────────
    win_dict["bounds"]["top_left"] = [win_x, win_y]
    win_dict["bounds"]["top_right"] = [win_x + win_w, win_y]
    win_dict["bounds"]["bottom_left"] = [win_x, win_y + win_h]
    win_dict["bounds"]["bottom_right"] = [win_x + win_w, win_y + win_h]
    win_dict["bounds"]["center"] = [win_cx, win_cy]

    # ── 获取显示器列表 ─────────────────────────────────────────────────────────
    monitors = _get_monitors()

    # ── 判断窗口落在哪个显示器 ─────────────────────────────────────────────────
    on_monitor, crossed_monitors = _calc_monitor_placement(
        win_x, win_y, win_w, win_h, monitors
    )

    logger.info(
        "macOS get_window_info | owner={} title={} foreground={} monitor={}",
        win_dict["owner"], win_dict["title"], is_foreground, on_monitor
    )

    return {
        "window": win_dict,
        "monitors": monitors,
        "monitor_count": len(monitors),
        "placement": {
            "on_monitor": on_monitor,
            "monitors_spanned": crossed_monitors,
            "spanned_count": len(crossed_monitors),
        },
    }


# ── 内部辅助函数 ────────────────────────────────────────────────────────────────


def _resolve_bundle_id(bundle_id: str) -> str | None:
    """
    将 macOS Bundle ID 解析为应用的本地化名称（localizedName）。

    优先用 AppKit NSRunningApplication，失败则用 osascript。

    Args:
        bundle_id: 应用 Bundle ID，如 "com.netease.mumu.nemux.emulator"

    Returns:
        应用名字符串，解析失败返回 None。
    """
    # 方式1：AppKit（PyObjC）
    try:
        from AppKit import NSRunningApplication
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
        if apps and len(apps) > 0:
            name = apps[0].localizedName()
            logger.debug("Bundle ID {} → {} (AppKit)", bundle_id, name)
            return name
    except ImportError:
        pass
    except Exception as e:
        logger.debug("AppKit 解析 Bundle ID 失败: {}", e)

    # 方式2：osascript（不依赖 PyObjC AppKit）
    import subprocess
    try:
        result = subprocess.run(
            ["osascript", "-e", f'tell app id "{bundle_id}" to get name'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            name = result.stdout.strip()
            logger.debug("Bundle ID {} → {} (osascript)", bundle_id, name)
            return name
    except Exception as e:
        logger.debug("osascript 解析 Bundle ID 失败: {}", e)

    return None


def _get_bundle_id_by_pid(pid: int) -> str | None:
    """
    通过进程 PID 获取应用的 Bundle ID。

    Args:
        pid: 进程 ID

    Returns:
        Bundle ID 字符串，找不到返回 None。
    """
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app:
            return app.bundleIdentifier()
    except (ImportError, Exception):
        pass
    return None


def _check_is_foreground(pid: int | None, bundle_id: str | None) -> tuple[bool, str]:
    """
    判断指定 PID 的应用是否为当前前台应用。

    Args:
        pid:       进程 ID
        bundle_id: Bundle ID（辅助比较用）

    Returns:
        (is_foreground: bool, frontmost_app_name: str)
    """
    try:
        from AppKit import NSWorkspace
        frontmost = NSWorkspace.sharedWorkspace().frontmostApplication()
        if frontmost:
            fm_pid = frontmost.processIdentifier()
            fm_bid = frontmost.bundleIdentifier()
            frontmost_name = frontmost.localizedName() or ""
            is_fg = (pid is not None and fm_pid == pid) or (
                bundle_id and fm_bid and fm_bid == bundle_id
            )
            return bool(is_fg), frontmost_name
    except (ImportError, Exception):
        pass
    return False, "unknown"


def _get_monitors() -> list[dict]:
    """
    获取所有显示器的几何信息（逻辑坐标）。

    Returns:
        显示器信息列表，每条包含 index / bounds / is_primary。
    """
    try:
        import mss
        with mss.mss() as sct:
            result = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue  # monitors[0] 是所有显示器合并，跳过
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
        # mss 未安装时返回单显示器占位
        return [{"index": 1, "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}, "is_primary": True}]


def _calc_monitor_placement(
    win_x: int, win_y: int, win_w: int, win_h: int,
    monitors: list[dict],
) -> tuple[int | None, list[int]]:
    """
    计算窗口的主显示器和跨屏情况。

    先用窗口中心点判断主显示器；
    再遍历所有显示器找出与窗口有重叠区域的显示器列表。

    Args:
        win_x, win_y: 窗口左上角逻辑坐标
        win_w, win_h: 窗口宽高
        monitors:     显示器列表（来自 _get_monitors()）

    Returns:
        (on_monitor: 主显示器编号 or None, crossed_monitors: 跨越的显示器编号列表)
    """
    cx = win_x + win_w // 2
    cy = win_y + win_h // 2

    # 主显示器：中心点落在哪个显示器
    on_monitor = None
    for mon in monitors:
        mb = mon["bounds"]
        if (mb["x"] <= cx <= mb["x"] + mb["width"] and
                mb["y"] <= cy <= mb["y"] + mb["height"]):
            on_monitor = mon["index"]
            break

    # 兜底：中心不在任何显示器，用左上角
    if on_monitor is None:
        for mon in monitors:
            mb = mon["bounds"]
            if (mb["x"] <= win_x < mb["x"] + mb["width"] and
                    mb["y"] <= win_y < mb["y"] + mb["height"]):
                on_monitor = mon["index"]
                break

    # 跨屏：找出所有与窗口有重叠的显示器
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
