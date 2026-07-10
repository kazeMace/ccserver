"""GameInstance：一局游戏的应用门面与聚合根（架构文档 §4）。

GameInstance 是 service / API 层唯一应该直接面对的对象。service 层不再直接操作
runtime、runner、event store、action service 或 actor runtime，而是通过 GameInstance
统一进入。

GameInstance 本身不写具体游戏规则；它协调：
  - GameRuntime：底层运行资源与生命周期（assign/start/pause/resume/step/terminate）。
  - SessionControl：会话过程（消息/动作/事件/进度/快照）。
  - ViewProjector：host/player/public 视图（当前复用 view_projection）。
  - SnapshotManager / RollbackManager：回滚（阶段4接入）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.control_plane.plane import build_control_plane
from drama_engine.core.control_plane.roles import ControlProposal
from drama_engine.core.game_instance.rollback import RollbackManager
from drama_engine.core.game_instance.session_control import SessionControl
from drama_engine.core.game_instance.snapshots import SnapshotManager
from drama_engine.core.interaction.projector import InteractionProjector
from drama_engine.core.views.projector import ViewProjector
from drama_engine.core.visibility.knowledge_firewall import (
    build_default_knowledge_firewall,
    build_knowledge_firewall_from_policy,
)

logger = logging.getLogger(__name__)


def _normalize_pack_specs(source: Any) -> list[dict[str, Any]]:
    """把 game_pack 声明归一为 spec 列表（单个 dict / 列表 / 字符串）。

    与 runner._normalize_pack_specs 同规则，供投影档案解析复用。
    """
    if source is None:
        return []
    items = source if isinstance(source, list) else [source]
    specs: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str) and item:
            specs.append({"plugin": item})
        elif isinstance(item, dict) and item.get("plugin"):
            specs.append(item)
    return specs


class GameInstance:
    """一局游戏的应用门面。

    通过 GameInstanceFactory 或 registry 创建；持有一个 GameRuntime，并在其之上
    暴露稳定的 service 层接口。
    """

    def __init__(self, runtime: Any) -> None:
        """绑定底层 GameRuntime，并在其会话状态上建立 SessionControl。"""
        assert runtime is not None, "runtime 不能为空"
        assert runtime.service is not None, "runtime.service 不能为空"
        self.runtime = runtime
        self.session_control = SessionControl(
            session_state=runtime.session,
            event_store=runtime.event_store,
            action_service=runtime.action_service,
        )
        # 让 SnapshotManager 能取到 runtime 轻量状态（phase 等）。
        self.session_control.runtime = runtime
        self.snapshots = SnapshotManager(
            session_control=self.session_control,
            state_provider=self._current_game_state,
            journal_provider=self._current_patch_journal,
            memory_provider=lambda: runtime.memory_store,
            disclosure_provider=self._current_disclosure_ledger,
        )
        self.rollback = RollbackManager(
            session_control=self.session_control,
            state_provider=self._current_game_state,
            journal_provider=self._current_patch_journal,
            memory_provider=lambda: runtime.memory_store,
            disclosure_provider=self._current_disclosure_ledger,
        )
        # 视图统一走 ViewProjector；信息隔离走 KnowledgeFirewall。
        self.views = ViewProjector(runtime)
        # firewall 先给默认（无秘密）实例；assign 后按脚本 visibility 声明重建。
        self.firewall = build_default_knowledge_firewall()
        # ControlPlane 在 assign 后按脚本 control_plane 声明构建（此时才有编译产物）。
        self.control_plane = None
        # interaction.v1 投影器：把内部事件/动作归一成对外协议对象。
        self.projector = InteractionProjector()
        # 回滚对齐（§6）：回滚后置为 checkpoint 消息游标，下一次 inbox 作为 reset_from 返回一次后清除。
        self._pending_reset_from: int | None = None
        logger.info("[GameInstance] 绑定 session=%s", self.session_id)

    def _current_game_state(self) -> Any:
        """返回当前游戏事实 engine.State；runner 未就绪时为 None。"""
        runner = getattr(self.runtime, "runner", None)
        return getattr(runner, "game_state", None) if runner is not None else None

    def _current_patch_journal(self) -> Any:
        """返回当前 patch journal；runner 未就绪时为 None。"""
        runner = getattr(self.runtime, "runner", None)
        return getattr(runner, "patch_journal", None) if runner is not None else None

    def _current_disclosure_ledger(self) -> Any:
        """返回当前披露账本；runner 未就绪时为 None。"""
        runner = getattr(self.runtime, "runner", None)
        return getattr(runner, "disclosure_ledger", None) if runner is not None else None

    def projection_profile(self) -> Any:
        """解析当前脚本声明的 game_pack 的对外投影档案并合并（interaction.v1 开放键）。

        无声明或声明的包无 profile 时返回空档案（projector 只填封闭键）。结果按 runner 缓存，
        避免每次 inbox 重复解析。projector 据此富化 widget/props；前端不再读死配置。
        """
        runner = getattr(self.runtime, "runner", None)
        if runner is None:
            from drama_engine.core.interaction.profile import EMPTY_PROFILE
            return EMPTY_PROFILE
        cached = getattr(runner, "_interaction_profile", None)
        if cached is not None:
            return cached
        from drama_engine.core.game_packs import build_default_game_pack_runtime_registry
        from drama_engine.core.interaction.profile import EMPTY_PROFILE, ProjectionProfile

        script = getattr(runner, "_script", None)
        if script is None:
            # script 未就绪（assign 尚未完成），不缓存，返回空
            return EMPTY_PROFILE
        registry = build_default_game_pack_runtime_registry()
        merged = ProjectionProfile()
        for spec in _normalize_pack_specs(getattr(script, "game_pack", None)):
            plugin_id = spec.get("plugin")
            if plugin_id and registry.has(plugin_id):
                profile = getattr(registry.get(plugin_id), "projection_profile", None)
                if profile is not None:
                    merged = merged.merge(profile)
        result = merged if merged.to_dict() != EMPTY_PROFILE.to_dict() else EMPTY_PROFILE
        setattr(runner, "_interaction_profile", result)
        return result

    # ---- 基本标识 ----

    @property
    def session_id(self) -> str:
        """返回本局 session id。"""
        return self.runtime.session.session_id

    @property
    def status(self) -> str:
        """返回会话生命周期状态。"""
        return self.runtime.session.status

    # ---- 生命周期（委托 GameRuntime）----

    async def assign(self, role_assignments: dict[str, str] | None = None) -> None:
        """执行发牌/初始化，并按脚本声明构建控制面与信息隔离层。

        参数:
            role_assignments: 可选的角色分配字典 {seat_id: role_name}
                            例如 {"Player_1": "nora", "Player_2": "marco"}
                            若不提供，则使用脚本 meta.recommended_player_role 的默认分配
        """
        await self.runtime.assign()
        self._build_control_plane()
        self._build_firewall()
        self._assign_roles(role_assignments)

    def _assign_roles(self, role_assignments: dict[str, str] | None) -> None:
        """根据 role_assignments 或脚本推荐，把角色分配写入 State。

        逻辑：
        1. 若提供了 role_assignments，按其分配
        2. 否则，读取 meta.recommended_player_role，分配给第一个真人玩家
        3. 若都没有，不做分配（角色可选的游戏）
        """
        runner = getattr(self.runtime, "runner", None)
        if runner is None:
            return

        state = getattr(runner, "game_state", None)
        if state is None:
            return

        script = getattr(runner, "_script", None)
        if script is None:
            return

        # 如果明确提供了 role_assignments，按其分配
        if role_assignments:
            from drama_engine.core.engine import StateWriter, SetAttr
            writer = StateWriter(state)
            for seat_id, role_name in role_assignments.items():
                writer.apply(SetAttr(seat_id, "role", role_name))
                logger.info(
                    "[GameInstance] 分配角色：%s → %s",
                    seat_id,
                    role_name,
                )
            return

        # 否则，尝试使用 meta.recommended_player_role 的默认分配
        meta = getattr(script, "raw", {}).get("meta", {})
        recommended_role = meta.get("recommended_player_role")
        if not recommended_role:
            return

        # 找到第一个真人玩家（human_seat_ids）
        human_seat_ids = getattr(self.runtime.session, "human_seat_ids", set())
        if not human_seat_ids:
            # 如果没有指定真人，默认给第一个 seat
            players = script.players or {}
            ids = players.get("ids") if isinstance(players, dict) else None
            if ids and len(ids) > 0:
                first_seat = str(ids[0])
            else:
                return
        else:
            first_seat = next(iter(human_seat_ids))

        # 分配推荐角色
        from drama_engine.core.engine import StateWriter, SetAttr
        writer = StateWriter(state)
        writer.apply(SetAttr(first_seat, "role", recommended_role))
        logger.info(
            "[GameInstance] 使用推荐角色分配：%s → %s",
            first_seat,
            recommended_role,
        )

    def _build_firewall(self) -> None:
        """按编译后脚本的 visibility 声明重建 KnowledgeFirewall。

        脚本未声明 visibility 时保持默认（无秘密、全部公开）。
        """
        runner = getattr(self.runtime, "runner", None)
        script = getattr(runner, "_script", None) if runner is not None else None
        policy = getattr(script, "visibility", None) if script is not None else None
        if policy is not None:
            self.firewall = build_knowledge_firewall_from_policy(policy)
            logger.info(
                "[GameInstance] 按脚本 visibility 构建 firewall，secret_attrs=%s",
                getattr(policy, "secret_attrs", ()),
            )

    def _build_control_plane(self) -> None:
        """按编译后脚本的 control_plane 声明构建 ControlPlane。

        应用器接入 patch/effect 提案落地；当前提供 patch 提案落到 patch journal 的
        最小应用器，effect/announcement 记录到事件流。
        """
        runner = getattr(self.runtime, "runner", None)
        script = getattr(runner, "_script", None) if runner is not None else None
        spec = getattr(script, "raw", {}).get("control_plane") if script is not None else None
        self.control_plane = build_control_plane(spec, applier=self._apply_control_proposal)

    def _apply_control_proposal(self, proposal: ControlProposal) -> None:
        """把已通过裁定的提案落到权威状态 / 执行层 / 事件流（M4）。

        patch 类提案若是 flow patch（add_scene/add_transition/set_state），经
        runner.apply_flow_patch 真正驱动 flow——此前只写 journal 却用错 record type，
        materializer 的 by_type("flow_patch") 收不到，提案形同虚设。其余 patch 仍入
        journal 存档。announcement 走公开事件流。
        """
        if proposal.kind == "patch":
            patch = dict(proposal.payload or {})
            runner = getattr(self.runtime, "runner", None)
            is_flow_patch = str(patch.get("type") or "") in {"add_scene", "add_transition", "set_state"}
            if is_flow_patch and runner is not None and hasattr(runner, "apply_flow_patch"):
                runner.apply_flow_patch(patch)
                return
            journal = self._current_patch_journal()
            if journal is not None:
                journal.append(str(patch.get("type") or "control_patch"), patch, source={"role": proposal.role})
        elif proposal.kind == "announcement":
            self.session_control.append_public({
                "kind": "control_announcement",
                "role": proposal.role,
                "text": proposal.payload.get("text", ""),
            })

    async def start(self) -> None:
        """启动本局。"""
        # 注入 snapshot_fn 到 runner 的 ctx（非序列化路径）
        runner = getattr(self.runtime, "runner", None)
        if runner is not None:
            ctx = getattr(runner, "_ctx", None)
            if ctx is not None and hasattr(ctx, "on_checkpoint"):
                ctx.on_checkpoint = self._auto_checkpoint_fn
        await self.runtime.start()

    async def pause(self) -> None:
        """暂停本局。"""
        await self.runtime.pause()

    async def resume(self) -> None:
        """恢复本局。"""
        await self.runtime.resume()

    async def step(self, count: int = 1) -> dict[str, Any]:
        """单步放行 count 个 step gate。"""
        assert count > 0, "count 必须大于 0"
        return await self.runtime.step(count=count)

    async def terminate(self, reason: str = "terminated") -> None:
        """终止本局。"""
        await self.runtime.terminate(reason=reason)

    async def restart(self) -> None:
        """清局并在同一 session 中重新发牌，并按脚本重建控制面与信息隔离层。

        重新发牌会重置游戏事实与角色分配，因此 firewall / control_plane 必须随之
        重建——否则重开局后可见性策略会停留在上一局或默认空实例（H1 缺口1）。
        """
        await self.runtime.restart()
        self._build_control_plane()
        self._build_firewall()

    # ---- 玩家加入 / 离开 ----

    def join_player(self, seat_id: str, user_id: str) -> None:
        """把 user_id 认领到指定 seat。"""
        assert seat_id, "seat_id 不能为空"
        assert user_id, "user_id 不能为空"
        seats = self.runtime.session.seats
        assert seat_id in seats, f"seat 不存在: {seat_id}"
        seats[seat_id].claimed_by = user_id
        logger.info("[GameInstance] 玩家加入 session=%s seat=%s", self.session_id, seat_id)

    def leave_player(self, seat_id: str, user_id: str) -> None:
        """把 user_id 从指定 seat 释放。"""
        assert seat_id, "seat_id 不能为空"
        seats = self.runtime.session.seats
        assert seat_id in seats, f"seat 不存在: {seat_id}"
        if seats[seat_id].claimed_by == user_id:
            seats[seat_id].claimed_by = None
            logger.info("[GameInstance] 玩家离开 session=%s seat=%s", self.session_id, seat_id)

    # ---- 动作 / 消息 ----

    async def submit_action(
        self,
        seat_id: str,
        payload: dict[str, Any] | None = None,
        source: str = "human",
        text: str = "",
    ) -> Any:
        """提交一个玩家动作到当前 pending request。"""
        return await self.session_control.submit_action(
            seat_id=seat_id,
            source=source,
            data=payload,
            text=text,
        )

    def pending_actions(self) -> list[dict[str, Any]]:
        """返回当前 pending 动作摘要。"""
        return self.session_control.pending_actions()

    def send_message(
        self,
        seat_id: str,
        text: str,
        scope: str = "public",
        to_seats: list[str] | None = None,
    ) -> dict[str, Any]:
        """发送一条会话消息（架构文档 §4/§15 的门面入口）。

        这是 service 层发消息的唯一入口，替代直接操作 event_store。消息经
        SessionControl 收口（cursor 随之同步），并按可见性路由：
          - scope="public"：公开广播，所有受众可见。
          - scope="private"：私密投递，仅 to_seats 指定的席位可见（如密谈/私聊）。
            未指定 to_seats 时默认只投给发送者自己。

        参数：
          seat_id  — 发送者席位（system 消息可传 "system"）。
          text     — 消息正文。
          scope    — "public" 或 "private"。
          to_seats — private 时的收件席位列表；None 时投给 seat_id 自己。
        返回：已发送的消息事件 dict。
        """
        assert seat_id, "seat_id 不能为空"
        assert text, "text 不能为空"
        assert scope in {"public", "private"}, "scope 必须是 public 或 private"
        event = {
            "kind": "interactive_message",
            "runtime_type": "interactive_session",
            "sender": seat_id,
            "text": text,
        }
        if scope == "public":
            self.session_control.append_public(dict(event))
        else:
            recipients = to_seats if to_seats else [seat_id]
            private_event = {**event, "visibility": "private"}
            for recipient in recipients:
                self.session_control.append_private(str(recipient), dict(private_event))
            # host 始终可观测私密消息（上帝视角），与 firewall 授权一致。
            self.session_control.append_host(dict(private_event))
        logger.info(
            "[GameInstance] send_message session=%s sender=%s scope=%s",
            self.session_id, seat_id, scope,
        )
        return event

    # ---- interaction.v1 三面（/inbox /reply /view）----

    def inbox(self, seat: str, after: int = 0) -> dict[str, Any]:
        """返回某受众的 InboxResponse（§5）。

        seat 形如 host | public | audience | player:<id>。可见性已由 SessionEventStore
        三受众分流保证（public/host/private），前端拿到的即是该看的全部、且仅该看的。
        """
        assert seat, "seat 不能为空"
        audience, seat_id = self._parse_seat(seat)
        events = self._events_for(audience, seat_id)
        pending = None
        if seat_id is not None:
            pending = self.runtime.action_service.get_current_request(seat_id)
        status = self.runtime.session.status
        phase = self.runtime.session.progress.current_scene if self.runtime.session.progress else None
        # 一次性消费回滚对齐信号：置回 reset_from 后清除，避免后续 inbox 重复触发客户端重拉。
        reset_from = self._pending_reset_from
        self._pending_reset_from = None
        return self.projector.build_inbox(
            events=events,
            after=after,
            pending_request=pending,
            status=status,
            self_seat=seat_id,
            phase=phase,
            reset_from=reset_from,
            profile=self.projection_profile(),
        )

    async def reply(self, seat: str, reply_payload: dict[str, Any]) -> dict[str, Any]:
        """处理 PlayerReply（§4）→ 落到 ActionRequestService.submit，返回 ReplyAck。

        reply_payload 含 request_id + choice_id/choice_ids/text/data 之一。
        choice_id/choice_ids 归一进 data，复用 submit 的候选/schema 校验。
        """
        _, seat_id = self._parse_seat(seat)
        assert seat_id, "reply 需要具体 seat（player:<id>）"
        # 防串号/防过期（§4）：若 reply 带 request_id，须与当前 pending 的 request_id 一致，
        # 否则拒绝——避免玩家用过期/错误的 request_id 覆盖新的待回复。
        req_id = str(reply_payload.get("request_id") or "")
        if req_id:
            current = self.runtime.action_service.get_current_request(seat_id)
            current_id = str(getattr(current, "request_id", "") or "") if current is not None else ""
            if current_id != req_id:
                return {
                    "accepted": False,
                    "error": f"request_id 不匹配当前待回复（可能已过期）：{req_id}",
                    "new_messages": [],
                }
        data = self._reply_to_data(reply_payload)
        text = str(reply_payload.get("text") or "")
        submission = await self.session_control.submit_action(
            seat_id=seat_id, source="human", data=data, text=text,
        )
        if submission is None:
            return {"accepted": False, "error": "无 pending 请求", "new_messages": []}
        validated = bool(getattr(submission, "validated", True))
        return {
            "accepted": validated,
            "error": getattr(submission, "validation_error", "") or None,
            "new_messages": [],
        }

    def view(self, seat: str) -> dict[str, Any]:
        """返回某受众的 StateView（§7），投影自 ViewProjector 快照。"""
        audience, seat_id = self._parse_seat(seat)
        if audience == "host":
            snap = self.views.host_view()
        elif seat_id is not None:
            snap = self.views.player_view(seat_id)
        else:
            snap = self.views.public_view()
        return self._snapshot_to_state_view(snap, seat_id or audience)

    def _parse_seat(self, seat: str) -> tuple[str, str | None]:
        """把 inbox/view 的 seat 参数解析成 (audience, seat_id)。

        player:<id> → ("private", id)；host → ("host", None)；其余 → ("public", None)。
        """
        if seat.startswith("player:"):
            return "private", seat[len("player:"):]
        if seat == "host":
            return "host", None
        return "public", None

    def _events_for(self, audience: str, seat_id: str | None) -> list[dict[str, Any]]:
        """按受众取已授权的事件 backlog。"""
        store = self.runtime.event_store
        if audience == "host":
            return store.host_backlog()
        if audience == "private" and seat_id is not None:
            # 私密受众：公开流 + 自己的私密流，按 seq 合并。
            merged = store.public_backlog() + store.private_backlog(seat_id)
            return sorted(merged, key=lambda e: int(e.get("seq") or 0))
        return store.public_backlog()

    def _reply_to_data(self, reply_payload: dict[str, Any]) -> dict[str, Any] | None:
        """把 PlayerReply 的 choice_id/choice_ids/data 归一成 submit 的 data。"""
        if reply_payload.get("data") is not None:
            return dict(reply_payload["data"])
        choice_id = reply_payload.get("choice_id")
        choice_ids = reply_payload.get("choice_ids")
        if choice_ids is not None:
            return {"targets": list(choice_ids)}
        if choice_id is not None:
            # 单选归一：vote/choose/target 字段通吃，交由 submit 的候选校验判定。
            return {"choice": choice_id, "choose": choice_id, "vote": choice_id, "target": choice_id}
        return None

    def _snapshot_to_state_view(self, snap: dict[str, Any], seat: str) -> dict[str, Any]:
        """把 ViewSnapshot dict 归一成 StateView（§7）。

        phase：用真实阶段（progress.phase，回退 current_scene），不是会话状态 running/ended。
        progress：从 SessionState.progress 组 {label,current,total}。
        panels：按 game_pack 投影档案 profile.panels 声明，从游戏状态提取（affinity/hand/stats）。
        """
        progress_state = self.runtime.session.progress
        phase = None
        prog = None
        if progress_state is not None:
            phase = progress_state.phase or progress_state.current_scene
            # total 从游戏状态读（多天/多轮游戏在 GAME.total_days/total_rounds 声明），无则 0。
            state = self._current_game_state()
            total = 0
            if state is not None:
                total = int(state.get_attr("GAME", "total_days") or state.get_attr("GAME", "total_rounds") or 0)
            prog = {"label": phase or "", "current": int(progress_state.round or 0), "total": total}
        seat_id = None if seat in {"host", "public", "audience"} else seat
        panels = self._extract_panels(seat_id)
        # 始终注入 SCENE 背景信息（供前端渲染背景图，不依赖 game_pack 声明）
        state = self._current_game_state()
        if state is not None:
            locations = state.get_attr("SCENE", "locations")
            if locations:
                panels["scene_bg"] = {
                    "locations": locations,
                    "current_location": state.get_attr("SCENE", "current_location") or "",
                }
        return {
            "seat_id": seat,
            "phase": phase,
            "progress": prog,
            "players": snap.get("seats") or [],
            "panels": panels,
            "self": snap.get("role_card") or {},
        }

    def _extract_panels(self, seat_id: str | None) -> dict[str, Any]:
        """按当前 game_pack 投影档案的 panels 声明，从游戏状态提取侧边栏面板数据。

        profile.panels 形如 {"affinity": {"source": "affinity_matrix"}, ...}。这里做通用提取：
        - affinity：读各实体的 affinity_<other> 属性，组 {seat: {other: value}}。
        - hand：读 seat_id 的 hand 属性（列表）。
        - stats：读 seat_id 的数值面板属性（hp/gold/level 等，由声明 attrs 指定）。
        未声明 panels 或状态缺失时返回空 dict（前端忽略）。视图层不认识具体游戏，只按声明取数。
        """
        profile = self.projection_profile()
        panels_spec = getattr(profile, "panels", None) or {}
        if not panels_spec:
            return {}
        state = self._current_game_state()
        if state is None:
            return {}
        result: dict[str, Any] = {}
        players = list(state.get_attr("GAME", "players") or [])
        for name, spec in panels_spec.items():
            source = spec.get("source") if isinstance(spec, dict) else str(spec)
            if source == "affinity_matrix":
                matrix: dict[str, dict[str, Any]] = {}
                for p in players:
                    row = {q: state.get_attr(p, f"affinity_{q}") for q in players
                           if state.get_attr(p, f"affinity_{q}") is not None}
                    if row:
                        matrix[p] = row
                result[name] = matrix
            elif source == "hand" and seat_id is not None:
                result[name] = list(state.get_attr(seat_id, "hand") or [])
            elif source == "stats" and seat_id is not None:
                attrs = spec.get("attrs", []) if isinstance(spec, dict) else []
                result[name] = {a: state.get_attr(seat_id, a) for a in attrs
                                if state.get_attr(seat_id, a) is not None}
            elif source == "story_tree":
                result[name] = self._build_story_tree_panel(state)
        return result

    def _build_story_tree_panel(self, state: Any) -> dict[str, Any]:
        """构建剧情分支树面板数据。

        从 State 读取进度，从脚本 metadata 读取完整 flow 结构，
        组装成前端可渲染的树形数据。
        """
        visited_nodes = list(state.get_attr("GAME", "visited_nodes") or [])
        choice_history = list(state.get_attr("GAME", "choice_history") or [])
        current_node = state.get_attr("GAME", "__current_flow_node") or ""

        # 从脚本 metadata 提取 flow 树结构
        # 使用 materialized flow（含 grow_flow 生成的场景），实时计算
        script_data = self.runtime.session.metadata.get("interactive_session", {})
        base_flow = script_data.get("base_flow") or {}

        # 尝试从 runner 实时 materialize（含最新 patches）
        runner = getattr(self.runtime, "runner", None)
        journal = getattr(runner, "patch_journal", None) if runner else None
        if journal and base_flow:
            from drama_engine.core.runtime.interactive_session.patch.materializer import FlowMaterializer
            script = getattr(runner, "_script", None)
            if script:
                materialized = FlowMaterializer().materialize(script, journal, base_flow)
                flow_def = materialized.get("flow") or {}
                scenes_def = materialized.get("scenes") or {}
            else:
                flow_def = base_flow.get("flow") or {}
                scenes_def = base_flow.get("scenes") or {}
        else:
            flow_def = base_flow.get("flow") or {}
            scenes_def = base_flow.get("scenes") or {}
        states_def = flow_def.get("states") or {}

        # 构建 nodes 列表
        nodes = []
        for state_id, state_spec in states_def.items():
            scene_ids = state_spec.get("scenes") or []
            # 从 scene 的 context.title 优先取标题，fallback 到 publication 文本前 30 字
            title = state_id
            synopsis = ""
            if scene_ids and scene_ids[0] in scenes_def:
                scene_spec = scenes_def[scene_ids[0]]
                ctx = scene_spec.get("context") or {}
                # 优先使用 context.title（生成场景或预设场景设置的剧情名）
                if ctx.get("title"):
                    title = str(ctx["title"])
                else:
                    pub = scene_spec.get("publication", {})
                    msgs = pub.get("messages") or []
                    if msgs and isinstance(msgs[0], dict):
                        content = msgs[0].get("content") or {}
                        if isinstance(content, dict):
                            title = (content.get("text") or state_id)[:30]
                # 大纲
                if ctx.get("synopsis"):
                    synopsis = str(ctx["synopsis"])
            node_data: dict[str, Any] = {
                "id": state_id,
                "title": title,
                "terminal": bool(state_spec.get("terminal")),
            }
            if synopsis:
                node_data["synopsis"] = synopsis
            nodes.append(node_data)

        # 构建 edges 列表（从 choices.to 和 transitions）
        edges = []
        for state_id, state_spec in states_def.items():
            scene_ids = state_spec.get("scenes") or []
            # 从 scene 的 controller_action.choices 提取分支边
            for scene_id in scene_ids:
                if scene_id not in scenes_def:
                    continue
                scene_spec = scenes_def[scene_id]
                ctrl = scene_spec.get("controller_action") or {}
                for choice in (ctrl.get("choices") or []):
                    target = choice.get("to")
                    if target:
                        edges.append({
                            "from": state_id,
                            "to": target,
                            "choice_id": choice.get("id", ""),
                            "choice_text": choice.get("text", ""),
                        })
            # 从 transitions 提取固定跳转边
            for trans in (state_spec.get("transitions") or []):
                target = trans.get("to")
                if target:
                    edges.append({"from": state_id, "to": target})

        return {
            "current_node": current_node,
            "visited_nodes": visited_nodes,
            "choice_history": choice_history,
            "tree": {"nodes": nodes, "edges": edges},
        }

    def submit_control_action(self, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        """提交控制角色动作（host/director/writer 等）。

        走 ControlPlane 的「提案 → 校验 → 应用」流水线；控制角色不直接改权威状态。
        payload 需含 kind（patch/effect/announcement/scene_beat）与提案内容。
        """
        assert self.control_plane is not None, "control_plane 未构建；请先 assign"
        proposal = ControlProposal(
            role=role,
            kind=str(payload.get("kind") or ""),
            payload=dict(payload.get("payload") or {}),
            reason=str(payload.get("reason") or ""),
        )
        verdict = self.control_plane.submit_proposal(proposal)
        return verdict.to_dict()

    def apply_control_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """直接提交一个 proposal dict（等价 submit_control_action 的字典入口）。"""
        assert self.control_plane is not None, "control_plane 未构建；请先 assign"
        obj = ControlProposal(
            role=str(proposal.get("role") or ""),
            kind=str(proposal.get("kind") or ""),
            payload=dict(proposal.get("payload") or {}),
            reason=str(proposal.get("reason") or ""),
        )
        return self.control_plane.submit_proposal(obj).to_dict()

    def control_proposals(self) -> list[dict[str, Any]]:
        """返回控制面提案审批历史。"""
        return self.control_plane.proposals() if self.control_plane is not None else []

    # ---- 视图（统一走 ViewProjector）----

    def host_view(self) -> dict[str, Any]:
        """返回主持人视图快照。"""
        return self.views.host_view()

    def public_view(self) -> dict[str, Any]:
        """返回公开观众视图快照。"""
        return self.views.public_view()

    def player_view(self, seat_id: str, user_id: str | None = None) -> dict[str, Any]:
        """返回指定 seat 的玩家视图快照。"""
        assert seat_id, "seat_id 不能为空"
        return self.views.player_view(seat_id, user_id)

    def audience_view(self) -> dict[str, Any]:
        """返回观众视图。"""
        return self.views.audience_view()

    # ---- 受限上下文投影（KnowledgeFirewall）----

    def project_context(self, audience: str, purpose: str) -> dict[str, Any]:
        """按 audience + purpose 生成受限上下文（信息隔离）。

        受限 audience（player/agent）会额外叠加该 actor 已被披露的动态事实
        （如预言家的验人结果），来源为 DisclosureLedger。
        """
        disclosed_facts = self._disclosed_facts_for(audience)
        return self.firewall.project_context(
            state=self._current_game_state(),
            audience=audience,
            purpose=purpose,
            disclosed_facts=disclosed_facts,
        )

    def _disclosed_facts_for(self, audience: str) -> dict[str, Any] | None:
        """解析 audience 对应 actor 已被披露的事实（player:/agent: 前缀）。"""
        ledger = self._current_disclosure_ledger()
        if ledger is None:
            return None
        actor = None
        for prefix in ("player:", "agent:"):
            if audience.startswith(prefix):
                actor = audience[len(prefix):]
                break
        if not actor:
            return None
        return ledger.facts_for(actor)

    # ---- Timeline / 事件 ----

    def timeline(self, audience: str, seat_id: str | None = None) -> list[dict[str, Any]]:
        """返回指定 audience 的事件 timeline 回放。"""
        assert audience in {"public", "host", "private"}, f"未知 audience: {audience}"
        if audience == "public":
            return self.session_control.public_backlog()
        if audience == "host":
            return self.session_control.host_backlog()
        assert seat_id, "private timeline 必须提供 seat_id"
        return self.session_control.private_backlog(seat_id)

    def events(self, audience: str, seat_id: str | None = None, subscribe: bool = False) -> Any:
        """返回事件回放或订阅对象（供 SSE 使用）。"""
        return self.runtime.events(audience, seat_id=seat_id, subscribe=subscribe)

    # ---- 摘要 ----

    def summary(self) -> dict[str, Any]:
        """返回 session 摘要。"""
        return self.runtime.summary()

    def seat_summary(self) -> list[dict[str, Any]]:
        """返回 seat 摘要。"""
        return self.runtime.seat_summary()

    # ---- 回滚（Checkpoint + append-only timeline）----

    def _auto_checkpoint_fn(self, reason: str) -> None:
        """自动 checkpoint 回调 — 供 runner 在进入新 state 时调用。"""
        try:
            self.snapshots.create_checkpoint(reason)
        except Exception:  # noqa: BLE001
            pass  # 自动 checkpoint 失败不应阻塞流程

    def checkpoint(self, reason: str) -> dict[str, Any]:
        """在当前时刻创建一个 checkpoint，返回其摘要。"""
        assert reason, "reason 不能为空"
        return self.snapshots.create_checkpoint(reason).to_summary()

    def rollback_points(self) -> list[dict[str, Any]]:
        """返回可回滚的 checkpoint 摘要列表。"""
        return self.snapshots.list_points()

    async def rollback_to(self, checkpoint_id: str) -> None:
        """回滚到指定 checkpoint，并停在「已就绪待重启」（assigned）状态。

        执行侧闭环（架构文档 §7/§15）：回滚会取消正在运行的 flow task，把会话恢复到
        checkpoint 时刻，然后**统一落在 assigned 状态**，由调用方显式 `start()` 重新推进。

        为什么不自动续跑：interactive_session 的 FlowExecutor 只能从 flow.initial 顺序执行，
        且 checkpoint 不快照「当前 flow 位置」与「scene 内部进度（如 openchat 轮次）」。因此在
        running 态回滚后重建 task 只会从头重跑，重复发消息与重复结算——比崩溃更隐蔽有害。
        回滚的正确语义是「回到过去某点、暂停、由操作者重新推进」，契合开发/试玩/剧情分支场景。
        （此前实现在 running 态直接调 runner.start() 会撞其 `assert status==assigned` 断言而崩溃。）
        """
        assert checkpoint_id, "checkpoint_id 不能为空"
        checkpoint = self.snapshots.get(checkpoint_id)

        # 1. 取消后台 flow task，避免回滚与执行竞争；保留 ctx 以便在恢复后的状态上重启。
        runner = getattr(self.runtime, "runner", None)
        if runner is not None and hasattr(runner, "cancel_task"):
            await runner.cancel_task()

        # 2. 恢复会话过程、游戏状态、patch journal、记忆、披露账本。
        self.rollback.restore(checkpoint, policy=self.runtime.session.rollback_policy)

        # 2.5 回滚对齐信号（§6）：记录恢复后的公开消息游标，下一次 inbox 置 reset_from。
        #     客户端据此丢弃 seq > reset_from 的本地消息、把 after 回退到该点重拉分支后的时间线。
        self._pending_reset_from = int(self.runtime.session.message_cursor or 0)

        # 3. 执行侧闭环：flow task 已取消。若恢复后的 status 表示「本应在跑」（running/paused），
        #    则降级为 assigned——语义为「已回到该点、待重新 start」，消除「状态说在跑、实际无 task」
        #    的不一致，也让后续 start() 的 assigned 断言成立。ended/failed/lobby 等终态不动。
        session = self.runtime.session
        if session.status in {"running", "paused"}:
            session.set_status("assigned")
            self.session_control.append_host({
                "kind": "rollback_ready_to_restart",
                "checkpoint_id": checkpoint_id,
                "message": "已回滚并停在 assigned 状态，请重新 start 以从该点推进流程。",
            })
            logger.info(
                "[GameInstance] 回滚后降级为 assigned，待重启 session=%s checkpoint=%s",
                self.session_id,
                checkpoint_id,
            )


__all__ = ["GameInstance"]
