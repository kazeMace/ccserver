---
name: screen-capture
description: 使用 ScreenCapture、WindowList、WindowInfo 工具进行屏幕截图和窗口信息查询，指导视觉任务的操作流程。
tags: [screen, vision, window, automation]
---

# 屏幕截图与窗口操作指南

## 工具概览

| 工具 | 用途 |
|------|------|
| `ScreenCapture` | 截取全屏或指定区域/窗口的截图 |
| `WindowList` | 列出当前所有可见窗口（标题、应用名、位置） |
| `WindowInfo` | 查询指定窗口的详细几何信息（bounds、中心坐标、所在显示器） |

## 标准操作流程

### 1. 查找目标窗口

```
先用 WindowList 列出所有窗口，找到目标窗口的 owner（macOS）或 proc（Windows）。
再用 WindowInfo 获取精确位置。
```

### 2. 截图策略选择

- **全屏截图**：适合快速了解屏幕状态，用 `action="fullscreen"`
- **窗口截图**：已知应用名时优先用 `app_name` 参数，精确且不受遮挡影响
- **区域截图**：已知坐标时用 `region=[x, y, width, height]`，最快

### 3. macOS 窗口匹配优先级

```
bundle_id（最精确）> app_name（kCGWindowOwnerName）> window_title（kCGWindowName）
```

游戏窗口、Electron 应用通常 `window_title` 为空，必须用 `app_name` 或 `bundle_id`。

### 4. 坐标系说明

- **macOS**：逻辑坐标（points），Retina 屏幕上 1 point = 2 物理像素
- **Windows**：物理像素坐标（已自动处理 DPI 感知）
- **Android**：设备屏幕物理像素坐标

## 典型任务示例

### 截取特定应用的窗口

```
1. WindowList → 确认应用正在运行，记录 owner/proc
2. ScreenCapture(action="app", app_name="Safari") → 获取截图
3. （可选）WindowInfo(app_name="Safari") → 获取窗口 bounds 用于后续点击
```

### 判断 UI 状态

```
1. ScreenCapture(action="fullscreen") → 截全屏
2. 分析截图中的 UI 元素状态（按钮是否高亮、弹窗是否出现）
3. 根据状态决定下一步操作（InputClick / InputType）
```

### Android 自动化视觉路径

```
1. ScreenCapture(action="android") → 截取 Android 设备屏幕
2. AndroidCtrl(action="ui_dump") → 获取 UI 节点树（文本/可点击元素/坐标）
3. 优先用 ui_dump 中的 center 坐标点击，无法识别时再用视觉截图定位
```

## 注意事项

- 截图包含敏感信息时（密码、私钥），不要在日志中打印 base64 原始数据
- 多显示器环境下，`region` 坐标是全局坐标系（跨屏幕连续）
- WindowInfo 返回的 `placement.on_monitor` 指示窗口主要在哪个显示器上
- Android 截图需要设备已通过 adb 连接，用 `AndroidCtrl(action="devices")` 确认
