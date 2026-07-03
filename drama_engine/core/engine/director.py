"""Director loop and setup services for executing a Script."""

from typing import Any

from .cast import Cast
from .dialogue import Narration, _resolve_cue, _resolve_publication_messages
from .models import ActorProfile, Role, Scene, Script
from .narration import Narrator
from .stage import Stage
from .state import SetAttr, State, StateWriter


class ActorProfileBuilder:
    """Build stable actor profiles from script role concepts."""

    def __init__(self, script: Script) -> None:
        assert script is not None, "script 不能为空"
        self.script = script

    def build_role_context_text(self, role: Role) -> str:
        """Build concept context text for one role."""
        concepts = self.script.concepts or {}
        if not isinstance(concepts, dict):
            return ""

        parts = []

        role_text = self._format_concept("roles", role.name)
        if role_text:
            parts.append(role_text)

        if role.faction:
            faction_text = self._format_concept("factions", role.faction)
            if faction_text:
                parts.append(faction_text)

        for ability in role.abilities:
            ability_text = self._format_concept("abilities", ability)
            if ability_text:
                parts.append(ability_text)

        for item_spec in (role.inventory or []):
            item_name = item_spec.get("item") if isinstance(item_spec, dict) else ""
            item_text = self._format_concept("items", item_name)
            if item_text:
                parts.append(item_text)

        if not parts:
            return ""

        return "【概念说明】\n" + "\n".join(parts)

    def build(self, actor: Any, role: Role) -> ActorProfile:
        """Build one stable ActorProfile."""
        actor_name = getattr(actor, "name", "")
        assert actor_name, "actor.name 不能为空，无法构造 ActorProfile"
        return ActorProfile(
            actor_name=actor_name,
            display_name=getattr(actor, "display_name", "") or actor_name,
            nickname=getattr(actor, "nickname", "") or "",
            role_name=role.name,
            role_display_name=role.display_name or role.name,
            faction=role.faction or "",
            brief=role.brief or "",
            role_context=self.build_role_context_text(role),
        )

    def _format_concept(self, group_name: str, concept_name: str) -> str:
        """Format one concept description."""
        if not concept_name:
            return ""

        concepts = self.script.concepts or {}
        group = concepts.get(group_name, {}) if isinstance(concepts, dict) else {}
        concept = group.get(concept_name, {}) if isinstance(group, dict) else {}
        if not isinstance(concept, dict):
            return ""

        display_name = concept.get("display_name", concept_name)
        description = concept.get("description", "")
        prompt = concept.get("prompt", "")

        text = f"- {display_name}({concept_name})：{description}"
        if prompt:
            text += f" 提示：{prompt}"
        return text


class CastingService:
    """Assign roles, write state, publish actor profiles, and subscribe scopes."""

    def __init__(
        self,
        script: Script,
        stage: Stage,
        cast: Cast,
        profile_builder: ActorProfileBuilder | None = None,
        profile_publisher: Any = None,
    ) -> None:
        assert script is not None, "script 不能为空"
        assert stage is not None, "stage 不能为空"
        assert cast is not None, "cast 不能为空"
        self.script = script
        self.stage = stage
        self.cast = cast
        self.profile_builder = profile_builder or ActorProfileBuilder(script)
        self.profile_publisher = profile_publisher
        self.assigned = False
        self.assignment: list = []

    def assign(self, state: State) -> list:
        """Execute opening casting and return actor-role assignments."""
        if self.assigned:
            return list(self.assignment)
        print("\n" + "="*60)
        print("【开场】选角发牌")
        print("="*60)
        writer = StateWriter(state)
        actor_names = self.cast.all_names()
        player_config = self.script.player_config

        if player_config is not None:
            self.cast.apply_player_config(player_config)
            initial_attrs = dict(player_config.initial_attrs or {})
        else:
            initial_attrs = {"alive": True}

        for seat_index, name in enumerate(actor_names, start=1):
            attrs = dict(initial_attrs)
            attrs.setdefault("seat_index", seat_index)
            state.register_entity(name, initial_attrs=attrs)

        assignment = self.script.casting.deal(actor_names, self.script.roles)

        for actor_name, role in assignment:
            actor = self.cast.get(actor_name)
            profile = self.profile_builder.build(actor, role)
            self._publish_actor_profile(actor, role, profile)

            writer.apply(SetAttr(actor_name, "role", role.name))
            writer.apply(SetAttr(actor_name, "role_display_name", role.display_name or role.name))
            writer.apply(SetAttr(actor_name, "faction", role.faction))

            for item_spec in (role.inventory or []):
                item = item_spec["item"]
                count = item_spec.get("count", 1)
                writer.apply(SetAttr(actor_name, f"inventory_{item}", count))

            assert role.name in self.script.vocab.roles, (
                f"角色 '{role.name}' 不在词汇表中"
            )

            self.stage.subscribe(actor_name, role.scopes)

            print(f"[Director] {actor_name} 被分配角色：{role.name}")

        print(f"[Director] 开场完成，演员：{actor_names}")
        self.assigned = True
        self.assignment = list(assignment)
        return list(self.assignment)

    def _publish_actor_profile(self, actor: Any, role: Role, profile: ActorProfile) -> None:
        """Publish one actor profile through the configured publisher."""
        if self.profile_publisher is not None:
            self.profile_publisher.publish(actor=actor, role=role, profile=profile)
            return
        if hasattr(actor, "set_actor_profile"):
            actor.set_actor_profile(profile)
        elif hasattr(actor, "set_role_snapshot"):
            actor.set_role_snapshot(role)


class Director:
    """
    导演 — 剧本的解释器和执行引擎。

    持有 Script、Stage、Narrator、Cast 的引用，
    按 Script.flow 的顺序推进幕场，直到 referee 判出胜负。

    使用方式：
      director = Director(script, stage, narrator, cast)
      result = await director.run(state)
    """

    def __init__(
        self,
        script: Script,
        stage: Stage,
        narrator: Narrator,
        cast: Cast,
        on_setup: Any = None,
        on_state_snapshot: Any = None,
        on_view_event: Any = None,
        gate: Any = None,
        casting_service: Any = None,
    ):
        """
        初始化导演。

        参数：
          script   — 要执行的剧本
          stage    — 舞台（消息路由器）
          narrator — 旁白（公告投递者）
          cast     — 演员池
          on_setup — 可选回调 def fn(state) -> None。开场（选角发牌 + 订阅）完成、
                     正式推进前调用一次。用于观测：此时角色已写入 State，
                     观战视图可据此立刻显示每人身份。None 时无影响。
          on_state_snapshot — 可选回调 def fn(state) -> None。Director 在对外
                     公布前调用，观察方主动读取当前 State 并生成展示快照。
          on_view_event — 可选回调 def fn(event) -> None。Director 在 publication
                     阶段生成结构化 ViewEvent 后调用，用于直播页插件卡片。
          gate     — 可选暂停/单步闸门。Director 会在每个关键流程步骤前等待。
        """
        self.script = script
        self.stage = stage
        self.narrator = narrator
        self.cast = cast
        self.on_setup = on_setup
        self.on_state_snapshot = on_state_snapshot
        self.on_view_event = on_view_event
        self.gate = gate
        self.casting_service = casting_service or CastingService(
            script=script,
            stage=stage,
            cast=cast,
        )
        self._setup_done = False

    async def _wait_step(self) -> None:
        """在暂停/单步模式下等待前端放行。"""
        if self.gate is not None:
            await self.gate.wait()

    def _publish_state_snapshot(self, state: State) -> None:
        """对外公布前，让观察方主动读取 State 并更新展示快照。"""
        if self.on_state_snapshot is not None:
            self.on_state_snapshot(state)

    def _publish_scene_views(self, scene: Scene, state: State) -> None:
        """把 scene.publication.views 投影成结构化事件并推给观察方。"""
        if self.on_view_event is None:
            return
        publication_views = (scene.publication or {}).get("views") or []
        if not publication_views:
            return
        registry = getattr(self.script, "plugin_registry", None)
        if registry is None:
            return

        from drama_engine.core.dsl.plugins import ViewContext

        for spec in publication_views:
            audience = spec.get("audience") or spec.get("scope") or scene.scope
            context = ViewContext(
                state=state,
                scene_name=scene.name,
                audience=audience,
                mutation_log=state.mutation_log(),
                script_extensions=getattr(self.script, "extensions", None) or {},
            )
            event = registry.project_view(spec, context)
            if event is not None:
                self.on_view_event(event)

    def _build_role_context_text(self, role: Role) -> str:
        """Compatibility wrapper around CastingService profile builder."""
        return self.casting_service.profile_builder.build_role_context_text(role)

    def _build_actor_profile(self, actor: Any, role: Role) -> ActorProfile:
        """Compatibility wrapper around CastingService profile builder."""
        return self.casting_service.profile_builder.build(actor, role)

    def _format_concept(self, group_name: str, concept_name: str) -> str:
        """Compatibility wrapper around CastingService profile builder."""
        return self.casting_service.profile_builder._format_concept(group_name, concept_name)

    async def setup(self, state: State) -> None:
        """
        执行开场发牌，不推进正式 scene。

        Execute setup only: register players, shuffle/deal roles, write role
        state, subscribe scopes, and notify observers through on_setup.

        参数：
          state — 世界状态（初始为空，Director 在开场阶段写入角色信息）

        异常：
          AssertionError — 角色分配与玩家数量不匹配，或角色不在 vocab 中。
        """
        if self._setup_done:
            return

        if not getattr(self.casting_service, "assigned", False):
            self.casting_service.assign(state)

        # 开场钩子：角色已分配并写入 State，正式推进前通知观测方（如展示身份）
        if self.on_setup is not None:
            self.on_setup(state)

        self._setup_done = True

    async def run_flow(self, state: State) -> str:
        """
        从已完成 setup 的状态继续推进正式 scene，直到 referee 判出胜负。

        Continue after setup and run the scene loop.

        参数：
          state — 已完成 setup 的世界状态。

        返回：
          胜负公告文本（referee 返回的字符串）
        """
        await self.setup(state)
        writer = StateWriter(state)
        await self._wait_step()

        # ── 推进循环 ────────────────────────────────────────────────────────
        round_count = 0
        MAX_ROUNDS = 100   # 安全上限，防止无限循环

        while round_count < MAX_ROUNDS:
            round_count += 1
            print(f"\n{'='*60}")
            print(f"【第 {round_count} 轮】")
            print(f"{'='*60}")
            await self._wait_step()

            # 从 flow 取本轮的所有幕
            scenes = self.script.flow.next_scenes(state)
            if hasattr(self.script.flow, "on_batch_start"):
                trigger_index = len(state.mutation_log())
                self.script.flow.on_batch_start(state, writer)
                self._run_triggers_since(trigger_index, state, writer)
                self._publish_state_snapshot(state)
                await self._drain_pending_broadcasts(state, writer)

            for scene in scenes:
                scene._gate = self.gate
                await self._wait_step()

                # ── 跳幕检查 0：场景触发条件 ────────────────────────────────
                # 用于“仅第一晚”“某角色仍存活”“某标记存在时才发生”等控制流。
                if scene.when is not None and not scene.when(state):
                    print(
                        f"[Director] 幕 '{scene.name}' 跳过（when 条件不满足）"
                    )
                    continue

                # ── 跳幕检查 1：空场跳幕 ────────────────────────────────────
                # participants(state) 为空集，且不是纯旁白幕 → 跳过
                if not isinstance(scene.dialogue_policy, Narration):
                    participant_names = scene.participants(state)
                    if not participant_names:
                        print(
                            f"[Director] 幕 '{scene.name}' 跳过（空场：participants 为空集）"
                        )
                        continue

                narration_effects_applied = False
                if isinstance(scene.dialogue_policy, Narration) and scene.on_result is not None:
                    await self._wait_step()
                    print(f"[Director] 预执行 Narration effects：'{scene.name}'")
                    trigger_index = len(state.mutation_log())
                    scene.on_result([], state, writer)
                    self._run_triggers_since(trigger_index, state, writer)
                    await self._drain_pending_broadcasts(state, writer)
                    narration_effects_applied = True

                # 对外公布前，观察方按当前 State 主动生成展示快照。
                # Narration effects 已经预执行时，不在主持人播报前公开快照。
                if not (isinstance(scene.dialogue_policy, Narration) and narration_effects_applied):
                    self._publish_state_snapshot(state)

                # ── 跳幕检查 2：空白跳幕 ────────────────────────────────────
                # 没有 effects 的 Narration 幕 cue 为空时跳过；纯结算幕允许无 cue。
                cue_text = _resolve_cue(scene.cue, state)
                publication_messages = _resolve_publication_messages(scene, state)
                if (
                    isinstance(scene.dialogue_policy, Narration)
                    and not cue_text
                    and not publication_messages
                    and not narration_effects_applied
                ):
                    print(
                        f"[Director] 幕 '{scene.name}' 跳过（空白：cue 为空字符串）"
                    )
                    continue

                # ── 正常执行本幕 ─────────────────────────────────────────────
                print(f"\n[Director] 开始幕：'{scene.name}'，scope='{scene.scope}'")

                # 主持人喊场：默认把 cue 投递给 Scope 内所有成员。
                # announce_response_cue=False 的行动幕不会广播 cue；cue 只进入 Actor.act/
                # ActionRequest，适合猎人是否开枪这类私密决策。
                if publication_messages:
                    for item in publication_messages:
                        await self._wait_step()
                        await self.narrator.say(item["text"], item["audience"], state)
                elif cue_text and getattr(scene, "announce_response_cue", True):
                    await self._wait_step()
                    await self.narrator.say(cue_text, scene.scope, state)

                # 调用 TurnPolicy 执行发言调度
                await self._wait_step()
                responses = await scene.dialogue_policy.run(scene, self.stage, state, self.cast)

                # 调用 on_result 更新世界状态
                # 注意：去掉 "and responses" —— Narration（无人发言）幕也要能改状态，
                # 例如狼人杀的「黎明结算死亡」「回合推进」是无发言者但必须执行的步骤。
                # 这与设计文档 §4 控制流伪代码一致（伪代码本就无条件调 on_result）。
                # 各 on_result 函数都写成能容忍空 responses。
                if scene.on_result is not None and not narration_effects_applied:
                    await self._wait_step()
                    print(f"[Director] 调用 on_result：'{scene.name}'（responses={len(responses)}）")
                    trigger_index = len(state.mutation_log())
                    scene.on_result(responses, state, writer)
                    self._run_triggers_since(trigger_index, state, writer)

                await self._wait_step()
                self._publish_state_snapshot(state)
                self._publish_scene_views(scene, state)
                await self._drain_pending_broadcasts(state, writer)

                # 幕内退出条件检查（until）
                if scene.until and scene.until(state):
                    print(f"[Director] 幕 '{scene.name}' until 条件满足，提前结束本幕")

                # ── 裁判检查 ─────────────────────────────────────────────────
                await self._wait_step()
                verdict = self._call_referee(state, hook="after_scene", scene=scene)
                if verdict is not None:
                    # 胜负已分，公布结果
                    print(f"\n{'='*60}")
                    print(f"【胜负已分】{verdict}")
                    print(f"{'='*60}")
                    await self._wait_step()
                    await self.narrator.say(verdict, "public", state)
                    return verdict

                if hasattr(self.script.flow, "after_scene"):
                    trigger_index = len(state.mutation_log())
                    should_continue = self.script.flow.after_scene(scene, state, writer)
                    self._run_triggers_since(trigger_index, state, writer)
                    self._publish_state_snapshot(state)
                    await self._drain_pending_broadcasts(state, writer)
                    if not should_continue:
                        print(
                            f"[Director] Flow 请求切换阶段，跳过当前批次剩余 scenes"
                        )
                        break

            if hasattr(self.script.flow, "after_batch"):
                trigger_index = len(state.mutation_log())
                self.script.flow.after_batch(state, writer)
                self._run_triggers_since(trigger_index, state, writer)
                self._publish_state_snapshot(state)
                await self._drain_pending_broadcasts(state, writer)

            # 检查 flow 是否继续循环
            if not self.script.flow.loop:
                print("[Director] Flow 不循环，剧目结束")
                return "剧目正常结束"

        print(f"[Director] 达到最大轮数 {MAX_ROUNDS}，强制结束")
        return "达到最大轮数，强制结束"

    async def run(self, state: State) -> str:
        """
        执行整出戏，直到 referee 判出胜负。

        流程：
          1. 开场：选角发牌 + 告知角色 + 订阅 Scope
          2. 推进循环：按 flow 顺序逐幕执行，每幕结束后问 referee
          3. 胜负判定：referee 返回非 None 时公布结果，退出

        参数：
          state — 世界状态（初始为空，Director 在开场阶段写入角色信息）

        返回：
          胜负公告文本（referee 返回的字符串）
        """
        await self.setup(state)
        return await self.run_flow(state)

    def _call_referee(self, state: State, hook: str, scene: Scene | None = None) -> str | None:
        """Call referee with hook metadata while preserving legacy callables."""
        try:
            return self.script.referee(state, hook=hook, scene=scene)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            return self.script.referee(state)

    def _run_triggers_since(self, start_index: int, state: State, writer: StateWriter) -> None:
        """
        执行脚本级触发器。

        触发器基于 State 的 mutation log 工作。若触发器 effects 又产生新 mutation，
        本方法会继续消费新增部分，从而支持情侣殉情等连锁规则。
        """
        triggers = self.script.triggers or []
        if not triggers:
            return
        cursor = start_index
        guard = 0
        while cursor < len(state.mutation_log()):
            guard += 1
            assert guard <= 20, "脚本级 triggers 连锁超过 20 轮，疑似无限循环"
            mutations = state.mutation_log()[cursor:]
            cursor = len(state.mutation_log())
            for trigger in triggers:
                trigger(mutations, state, writer)

    async def _drain_pending_broadcasts(self, state: State, writer: StateWriter) -> None:
        """
        投递 effects.broadcast 产生的待发消息。

        EffectExecutor 只负责写 State；Director 在幕结束后统一把队列清空并投递，
        保持「状态变更」和「消息路由」职责分离。
        """
        pending = state.get_attr("GAME", "__pending_broadcasts") or []
        if not pending:
            return

        writer.apply(SetAttr("GAME", "__pending_broadcasts", []))
        for item in pending:
            scope = item.get("scope")
            text = item.get("template", "")
            if scope and text:
                await self.narrator.say(text, scope, state)
