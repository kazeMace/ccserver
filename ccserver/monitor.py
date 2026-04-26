"""
monitor.py — CCServer 监控 Dashboard（阶段1：纯 HTML + JS）。

架构说明 / Architecture
────────────────────────────────────────────────────────────────────────────
本模块属于渐进式前端架构的阶段1：
  - 阶段1（当前）：纯 HTML + CSS + JS，零前端构建工具
  - 阶段2（未来）：统一迁移到 Vite + React + TypeScript

前端文件位置：项目根目录 web/index.html
日志过滤：所有 monitor 日志带有 [Monitor] 前缀，可通过环境变量控制：
  - CCSERVER_MONITOR_LOG=0：关闭 debug 级别日志（默认开启）
  - grep "[Monitor]" 可筛选/排除 monitor 相关日志

技术要点
  - EventBus 是 Session 级别的，监控页需要聚合所有 Session 的事件
  - 实现方式：后台协程定期扫描 session_manager._sessions
    为新发现的 Session 创建 EventBus 订阅者
  - 前端通过单个 WebSocket 连接接收所有事件

数据流
  AgentEvent → Session.event_bus → MonitorCollector._subscriptions
             → WebSocket JSON → 前端展示
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import WebSocket
from loguru import logger

from ccserver.event_bus import AgentEvent


# ─── 日志控制 ─────────────────────────────────────────────────────────────────
# 环境变量 CCSERVER_MONITOR_LOG=0 可关闭 Monitor 的 debug 级别日志
# info / warning / error 级别始终输出，因为它们是运维关键信息
_MONITOR_LOG_ENABLED: bool = os.environ.get("CCSERVER_MONITOR_LOG", "1") == "1"


def _ml(level: str, message: str, *args, **kwargs) -> None:
    """
    Monitor 专用日志函数，统一添加 [Monitor] 标记前缀。

    Args:
        level:   日志级别，如 "debug", "info", "warning", "error"
        message: 日志消息内容（不含前缀），支持 loguru 风格格式化占位符 {}
        *args:   格式化参数，按顺序填充 message 中的占位符
        **kwargs: 额外参数，传递给 loguru 的 logger 方法

    过滤方式：
        - 环境变量：CCSERVER_MONITOR_LOG=0 关闭 debug 日志
        - grep：    grep "[Monitor]" 筛选 / grep -v "[Monitor]" 排除
    """
    # debug 级别受环境变量控制
    if level == "debug" and not _MONITOR_LOG_ENABLED:
        return

    log_fn = getattr(logger, level)
    log_fn(f"[Monitor] {message}", *args, **kwargs)


# ─── 前端文件路径 ─────────────────────────────────────────────────────────────
# 从 ccserver/monitor.py 出发，上级目录即为项目根目录
_WEB_ROOT = Path(__file__).parent.parent / "web"
_MONITOR_HTML_PATH = _WEB_ROOT / "index.html"


# ─── MonitorCollector：监控数据收集器 ──────────────────────────────────────────


class MonitorCollector:
    """
    监控页 WebSocket 数据收集器。

    职责：
      1. 遍历 session_manager 中所有 Session，为每个 Session 的 EventBus 创建订阅者
      2. 将收到的事件通过 WebSocket 推送给前端
      3. 定期扫描新 Session，自动订阅其 EventBus
      4. 定期推送全局状态（Session 列表、Agent 列表、Team 列表）
      5. WebSocket 断开时清理所有订阅，防止泄漏

    线程安全：
      本类仅用于 asyncio 协程，所有方法应在同一事件循环中调用。
    """

    # 扫描新 Session 的间隔（秒）
    SCAN_INTERVAL: float = 10.0
    # 推送全局状态的间隔（秒）
    STATE_INTERVAL: float = 30.0
    # EventBus 订阅者队列容量
    QUEUE_MAXSIZE: int = 512

    def __init__(self, session_manager, websocket: WebSocket):
        """
        初始化收集器。

        Args:
            session_manager: SessionManager 实例，用于遍历所有 Session
            websocket:       FastAPI WebSocket 连接对象
        """
        assert session_manager is not None, "session_manager cannot be None"
        assert websocket is not None, "websocket cannot be None"

        self._session_manager = session_manager
        self._websocket = websocket

        # session_id -> (Subscription, asyncio.Task) 的映射
        # Subscription 通过 async with 管理，Task 是订阅协程的引用
        self._subscriptions: dict[str, tuple] = {}

        # 后台协程任务引用
        self._scan_task: asyncio.Task | None = None
        self._state_task: asyncio.Task | None = None

        # 关闭标志，防止 stop() 后仍有回调尝试发送
        self._closed: bool = False

        _ml("debug", "MonitorCollector initialized")

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        启动收集器。

        执行顺序：
          1. 立即订阅当前所有已存在的 Session
          2. 启动扫描协程（发现新 Session 时自动订阅）
          3. 启动状态推送协程（定期向前端推送全局状态）
        """
        _ml("info", "MonitorCollector starting")

        # 1. 订阅现有 Session
        await self._subscribe_all_sessions()

        # 2. 启动后台协程
        self._scan_task = asyncio.create_task(self._scan_loop())
        self._state_task = asyncio.create_task(self._state_loop())

        _ml("info", "MonitorCollector started | sessions={}", len(self._subscriptions))

    async def stop(self) -> None:
        """
        停止收集器，清理所有资源。

        执行顺序：
          1. 设置关闭标志
          2. 取消扫描和状态推送协程
          3. 取消所有 EventBus 订阅协程（Subscription 的 __aexit__ 会自动 unsubscribe）
        """
        if self._closed:
            return
        self._closed = True
        _ml("info", "MonitorCollector stopping | subscriptions={}", len(self._subscriptions))

        # 取消后台协程
        for task in (self._scan_task, self._state_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._scan_task = None
        self._state_task = None

        # 取消所有 EventBus 订阅
        # 注意：Subscription 对象通过 async with 使用，
        # 取消其所在的协程任务后，协程结束会自动触发 __aexit__ 从而 unsubscribe。
        # 因此我们只需要取消对应的 task 即可。
        for session_id, (sub, task) in list(self._subscriptions.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            _ml("debug", "MonitorCollector: unsubscribed | session_id={}", session_id)

        self._subscriptions.clear()
        _ml("info", "MonitorCollector stopped")

    # ── 订阅管理 ──────────────────────────────────────────────────────────────

    async def _subscribe_all_sessions(self) -> None:
        """遍历 session_manager 中的所有 Session，为每个 Session 创建 EventBus 订阅。"""
        sessions = list(self._session_manager._sessions.values())
        for session in sessions:
            await self._subscribe_session(session)

    async def _subscribe_session(self, session) -> None:
        """
        为单个 Session 的 EventBus 创建订阅者。

        如果该 Session 已经被订阅，则跳过（幂等）。
        """
        session_id = session.id
        if session_id in self._subscriptions:
            return  # 已订阅，跳过

        event_bus = getattr(session, "event_bus", None)
        if event_bus is None:
            _ml("debug", "MonitorCollector: session has no event_bus | session_id={}", session_id[:8])
            return

        sub_id = f"monitor_{session_id[:8]}_{uuid.uuid4().hex[:4]}"

        # filter_fn=None 表示接收该 Session 的所有事件
        sub = event_bus.subscribe(sub_id, filter_fn=None, maxsize=self.QUEUE_MAXSIZE)

        # 启动订阅协程：持续从 Subscription 获取事件并转发
        task = asyncio.create_task(self._subscription_loop(session_id, sub))

        self._subscriptions[session_id] = (sub, task)
        _ml(
            "debug",
            "MonitorCollector: subscribed | session_id={} sub_id={}",
            session_id[:8], sub_id,
        )

    async def _subscription_loop(self, session_id: str, sub) -> None:
        """
        单个 Session 的 EventBus 订阅协程。

        持续从 Subscription 队列获取事件，格式化为 JSON 后通过 WebSocket 发送。
        当协程被取消（WebSocket 断开或 stop() 调用）时，
        Subscription 的 __aexit__ 会自动执行 unsubscribe。
        """
        try:
            async with sub:
                while True:
                    try:
                        event = await sub.get(timeout=1.0)
                    except asyncio.CancelledError:
                        break

                    if event is None:
                        # 超时：检查是否已关闭
                        if self._closed:
                            break
                        continue

                    await self._send_event(session_id, event)
        except asyncio.CancelledError:
            # 正常取消，由 stop() 触发
            pass
        except Exception as e:
            _ml(
                "error",
                "MonitorCollector: subscription loop error | session_id={} error={}",
                session_id[:8], e,
            )

    async def _send_event(self, session_id: str, event: AgentEvent) -> None:
        """
        将单个 AgentEvent 序列化为 JSON 并通过 WebSocket 发送。

        Args:
            session_id: 事件来源的 Session ID
            event:      AgentEvent 实例
        """
        if self._closed:
            return

        payload = {
            "type": "event",
            "data": {
                "session_id": session_id,
                "agent_id": event.agent_id,
                "sender_type": event.sender_type,
                "event_type": event.type,
                "payload": event.payload,
                "ts": event.ts,
                "event_id": event.event_id,
            },
        }

        try:
            await self._websocket.send_json(payload)
        except Exception as e:
            # WebSocket 可能已关闭，记录日志但不抛异常
            _ml("debug", "MonitorCollector: send failed, will reconnect | error={}", e)

    # ── 扫描协程 ──────────────────────────────────────────────────────────────

    async def _scan_loop(self) -> None:
        """
        定期扫描 session_manager，发现新 Session 时自动订阅。

        间隔由 SCAN_INTERVAL 控制。
        """
        try:
            while True:
                await asyncio.sleep(self.SCAN_INTERVAL)
                if self._closed:
                    break
                await self._subscribe_all_sessions()
        except asyncio.CancelledError:
            _ml("debug", "MonitorCollector: scan loop cancelled")
        except Exception as e:
            _ml("error", "MonitorCollector: scan loop error | error={}", e)

    # ── 状态推送协程 ──────────────────────────────────────────────────────────

    async def _state_loop(self) -> None:
        """
        定期向前端推送全局状态快照。

        包括：Session 列表、Agent 列表、Team 列表、EventBus 订阅者统计。
        """
        try:
            while True:
                await asyncio.sleep(self.STATE_INTERVAL)
                if self._closed:
                    break
                await self._send_state()
        except asyncio.CancelledError:
            _ml("debug", "MonitorCollector: state loop cancelled")
        except Exception as e:
            _ml("error", "MonitorCollector: state loop error | error={}", e)

    async def _send_state(self) -> None:
        """构建并发送当前全局状态快照。"""
        if self._closed:
            return

        sessions_data = []
        agents_data = []
        teams_data = []
        cron_tasks_data = []
        total_subscribers = 0

        for session in self._session_manager._sessions.values():
            sessions_data.append({
                "id": session.id,
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "msg_count": len(session.messages),
            })

            # 构建 agent_id -> team_name 映射（用于溯源）
            agent_team_map: dict[str, str] = {}
            registry = getattr(session, "team_registry", None)
            if registry is not None:
                for team in registry.list_teams():
                    for member_id in team.members.keys():
                        agent_team_map[member_id] = team.name

            # 根 Agent（如果存在）
            root_agent = getattr(session, "root_agent", None)
            if root_agent is not None:
                agents_data.append({
                    "id": root_agent.context.agent_id,
                    "task_id": None,
                    "name": root_agent.context.name or "orchestrator",
                    "status": root_agent.state.phase,
                    "session_id": session.id,
                    "parent_id": None,
                    "team_name": agent_team_map.get(root_agent.context.agent_id),
                })

            # Agent 任务（后台子 Agent）
            agent_tasks = getattr(session, "agent_tasks", None)
            if agent_tasks is not None:
                for task in agent_tasks.list_all():
                    agents_data.append({
                        "id": task.agent_id,
                        "task_id": task.id,
                        "name": task.agent_name,
                        "status": task.status,
                        "session_id": session.id,
                        "parent_id": task.parent_id,
                        "team_name": agent_team_map.get(task.agent_id),
                    })

            # Team
            if registry is not None:
                for team in registry.list_teams():
                    teams_data.append({
                        "name": team.name,
                        "member_count": len(team.members),
                    })

            # ScheduledTask 定时任务
            cron_scheduler = getattr(session, "cron_scheduler", None)
            if cron_scheduler is not None:
                for task in cron_scheduler.list_all():
                    cron_tasks_data.append({
                        "task_id": task.task_id,
                        "session_id": session.id,
                        "trigger_type": task.trigger_type,
                        "schedule": (
                            task.cron_expr
                            if task.is_cron
                            else (f"every {task.interval_seconds}s" if task.is_interval else "")
                        ),
                        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
                        "trigger_count": task.trigger_count,
                        "max_triggers": task.max_triggers,
                        "end_time": task.end_time.isoformat() if task.end_time else None,
                        "enabled": task.enabled,
                        "status": task.status,
                        "durable": task.durable,
                        "jitter_max": task.jitter_max,
                        "prompt": task.prompt[:50] + "..." if len(task.prompt) > 50 else task.prompt,
                    })

            # EventBus 订阅者统计
            event_bus = getattr(session, "event_bus", None)
            if event_bus is not None:
                total_subscribers += event_bus.subscriber_count()

        payload = {
            "type": "state",
            "data": {
                "sessions": sessions_data,
                "agents": agents_data,
                "teams": teams_data,
                "cron_tasks": cron_tasks_data,
                "subscriber_count": total_subscribers,
            },
        }

        try:
            await self._websocket.send_json(payload)
        except Exception as e:
            _ml("debug", "MonitorCollector: state send failed | error={}", e)


# ─── 便捷函数 ─────────────────────────────────────────────────────────────────


def get_monitor_html() -> str:
    """
    返回监控页面的 HTML 字符串。

    从项目根目录 web/index.html 读取内容。
    阶段2 迁移到 React 后，本函数将被移除，由 StaticFiles 直接托管。

    Returns:
        完整的 HTML 页面内容，可直接作为 HTTP 响应体返回。

    Raises:
        FileNotFoundError: 如果 web/index.html 不存在
    """
    if not _MONITOR_HTML_PATH.exists():
        _ml(
            "warning",
            "Monitor HTML not found at {} | falling back to error message",
            _MONITOR_HTML_PATH,
        )
        return "<h1>CCServer Monitor</h1><p>Monitor page not found. Please check web/index.html exists.</p>"

    content = _MONITOR_HTML_PATH.read_text(encoding="utf-8")
    _ml("debug", "Monitor HTML loaded | path={} size={} bytes", _MONITOR_HTML_PATH, len(content))
    return content
