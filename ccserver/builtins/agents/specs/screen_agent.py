"""
specs/screen_agent.py -- ScreenAgentSpec 视觉控制 Agent。

定位：通过截图感知屏幕状态，使用视觉 AI 定位 GUI 元素，执行鼠标/键盘操作。
适用场景：
  - 桌面 RPA 自动化（macOS / Windows）
  - Android 设备控制（通过 ADB）
  - 游戏 AI 挂机助手
  - 屏幕内容信息提取

工具集：ScreenCapture → ScreenFind → InputClick → InputType（感知 → 定位 → 操作）
"""

from ..base import BaseAgentSpec


class ScreenAgentSpec(BaseAgentSpec):
    """
    视觉感知与机器控制 Agent。

    能力：截图 → VLM 识别元素坐标 → 鼠标/键盘操作。
    支持 macOS desktop、Windows desktop、Android（ADB）三平台。

    工作模式（ReAct 循环）：
      1. ScreenCapture() 获取当前屏幕状态
      2. ScreenFind(description="目标元素") 获取元素坐标
      3. InputClick(x=..., y=...) 点击 / InputType(text="...") 输入
      4. 重复直到任务完成

    注意：每次视觉操作后应等待界面响应再截图确认结果。
    """

    # -- 标识 --
    name = "screen-agent"
    description = (
        "视觉感知与机器控制 Agent，能截图识别 GUI 元素并执行鼠标键盘操作。"
        "适用于桌面自动化（macOS/Windows）、Android 控制、游戏 AI、屏幕 RPA 等场景。"
        "使用 ScreenCapture 截图、ScreenFind 定位元素、InputClick 点击、InputType 输入。"
    )

    # -- 工具集（感知 + 操作 + 辅助） --
    tools = [
        # 视觉感知与控制
        "ScreenCapture",
        "ScreenFind",
        "InputClick",
        "InputType",
        # 辅助工具
        "Bash",       # 可执行系统命令（如启动程序、查询进程）
        "AskUser",    # 任务不明确时询问用户
    ]

    # -- 模型：使用 sonnet（需要多模态能力理解截图） --
    model_hint = "sonnet"

    # -- 运行限制 --
    round_limit = 50    # 自动化任务可能步骤较多
    max_turns = 100

    # -- 自动审批视觉操作工具（减少频繁中断）--
    auto_approve_tools = True
