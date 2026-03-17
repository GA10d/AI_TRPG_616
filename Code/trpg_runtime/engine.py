from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Sequence

from text_model.function_TextGeneration import Message, get_normal_reply

from .models import (
    DicerOutput,
    DirectorOutput,
    DirectorState,
    GameMeta,
    GameState,
    NPCManagerOutput,
    NpcState,
    ParsedPlayerAction,
    PlayerState,
    SceneState,
    TurnDebugTrace,
    TurnResult,
)
from .prompt_loader import PromptRepository, ScenarioBundle
from .state_store import advance_clock, append_recent_events, apply_delta, format_game_time
from .structured_output import generate_structured_output


ACTION_PATTERNS: list[tuple[str, Sequence[str]]] = [
    ("investigate", ("观察", "查看", "检查", "调查", "搜索", "探索")),
    ("social", ("询问", "交谈", "对话", "劝说", "说服", "威胁")),
    ("combat", ("攻击", "砍", "刺", "射击", "挥", "打")),
    ("movement", ("前往", "进入", "离开", "靠近", "穿过", "后退")),
    ("stealth", ("潜行", "躲", "隐藏", "偷", "悄悄")),
    ("use_item", ("使用", "拿出", "举起", "点燃", "打开")),
]


TARGET_PATTERN = re.compile(
    r"(?:观察|查看|检查|调查|搜索|探索|询问|攻击|进入|前往|使用|打开|靠近)([^，。；,.]{1,18})"
)


@dataclass(frozen=True)
class RuntimeOptions:
    dicer_rule_chars: int = 4200
    dicer_story_chars: int = 4200
    npc_rule_chars: int = 2600
    npc_story_chars: int = 2200
    director_rule_chars: int = 2600
    director_story_chars: int = 2200
    narrator_rule_chars: int = 1800
    narrator_story_chars: int = 2200
    opening_rule_chars: int = 2600
    opening_story_chars: int = 2600
    recent_event_window: int = 5
    background_npc_window: int = 4
    minutes_per_turn: int = 5
    max_recent_events: int = 5
    dicer_temperature: float = 0.2
    npc_temperature: float = 0.5
    director_temperature: float = 0.4
    narrator_temperature: float = 0.7
    max_parallel_workers: int = 3


def parse_player_action(player_text: str) -> ParsedPlayerAction:
    normalized = " ".join(player_text.split())
    intent = "general"
    for candidate_intent, keywords in ACTION_PATTERNS:
        if any(keyword in normalized for keyword in keywords):
            intent = candidate_intent
            break

    target_match = TARGET_PATTERN.search(normalized)
    target = target_match.group(1).strip() if target_match else None

    tags: list[str] = []
    if any(token in normalized for token in ("悄悄", "潜行", "安静")):
        tags.append("quiet")
    if any(token in normalized for token in ("快速", "立刻", "冲", "猛地")):
        tags.append("rush")
    if any(token in normalized for token in ("对话", "询问", "说", "劝")):
        tags.append("social")

    approach = None
    if "quiet" in tags:
        approach = "stealthy"
    elif "rush" in tags:
        approach = "aggressive"
    elif "social" in tags:
        approach = "social"

    return ParsedPlayerAction(
        raw_text=normalized,
        intent=intent,
        target=target,
        approach=approach,
        tags=tags,
    )


def _build_initial_npc_registry(
    *,
    visible_npcs: list[str] | None,
    npc_states: dict[str, dict[str, object]] | None,
    location: str,
) -> dict[str, NpcState]:
    visible_set = set(visible_npcs or [])
    registry: dict[str, NpcState] = {}

    for npc_name, raw_state in (npc_states or {}).items():
        payload = {"name": npc_name, "location": location, "is_visible": npc_name in visible_set}
        payload.update(raw_state)
        registry[npc_name] = NpcState.model_validate(payload)

    for npc_name in visible_set:
        if npc_name not in registry:
            registry[npc_name] = NpcState(
                name=npc_name,
                location=location,
                is_visible=True,
            )
        else:
            registry[npc_name] = registry[npc_name].model_copy(update={"is_visible": True})

    return registry


def create_initial_state(
    *,
    scenario: ScenarioBundle,
    player_name: str = "玩家",
    session_id: str | None = None,
    scene_id: str = "opening",
    location: str | None = None,
    scene_description: str | None = None,
    visible_npcs: list[str] | None = None,
    interactive_objects: list[str] | None = None,
    hazards: list[str] | None = None,
    inventory: list[str] | None = None,
    known_clues: list[str] | None = None,
    npc_states: dict[str, dict[str, object]] | None = None,
) -> GameState:
    resolved_location = location or scenario.title
    registry = _build_initial_npc_registry(
        visible_npcs=visible_npcs,
        npc_states=npc_states,
        location=resolved_location,
    )

    return GameState(
        meta=GameMeta(
            session_id=session_id or uuid.uuid4().hex[:12],
            rule_code=scenario.rule_code,
            story_code=scenario.story_code,
            scene_id=scene_id,
        ),
        player=PlayerState(
            name=player_name,
            inventory=inventory or [],
            known_clues=known_clues or [],
        ),
        scene=SceneState(
            location=resolved_location,
            description=scene_description or scenario.opening_scene,
            visible_npcs=visible_npcs or [name for name, npc in registry.items() if npc.is_visible],
            interactive_objects=interactive_objects or [],
            hazards=hazards or [],
        ),
        npcs=registry,
        director=DirectorState(),
        scenario_title=scenario.title,
        scenario_brief=scenario.story_summary,
        chapter_summary=scenario.story_summary,
    )


class MinimalTRPGEngine:
    def __init__(
        self,
        *,
        scenario: ScenarioBundle,
        state: GameState,
        prompt_repository: PromptRepository | None = None,
        preference_path: str | None = None,
        registry_path: str | None = None,
        options: RuntimeOptions = RuntimeOptions(),
    ) -> None:
        self.scenario = scenario
        self.state = state
        self.prompt_repository = prompt_repository or PromptRepository()
        self.preference_path = preference_path
        self.registry_path = registry_path
        self.options = options
        self._state_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=self.options.max_parallel_workers)
        self._pending_director_future: Future[tuple[dict[str, object], DirectorOutput, int]] | None = None
        self._last_director_context: dict[str, object] | None = None
        self._last_director_result: DirectorOutput | None = None
        self._unread_director_result: DirectorOutput | None = None

    @classmethod
    def from_prompt_files(
        cls,
        *,
        rule_code: str,
        story_code: str,
        player_name: str = "玩家",
        prompt_repository: PromptRepository | None = None,
        preference_path: str | None = None,
        registry_path: str | None = None,
        options: RuntimeOptions = RuntimeOptions(),
        scene_id: str = "opening",
        location: str | None = None,
        scene_description: str | None = None,
        visible_npcs: list[str] | None = None,
        interactive_objects: list[str] | None = None,
        hazards: list[str] | None = None,
        inventory: list[str] | None = None,
        known_clues: list[str] | None = None,
        npc_states: dict[str, dict[str, object]] | None = None,
    ) -> "MinimalTRPGEngine":
        repo = prompt_repository or PromptRepository()
        scenario = repo.load_scenario(rule_code=rule_code, story_code=story_code)
        state = create_initial_state(
            scenario=scenario,
            player_name=player_name,
            scene_id=scene_id,
            location=location,
            scene_description=scene_description,
            visible_npcs=visible_npcs,
            interactive_objects=interactive_objects,
            hazards=hazards,
            inventory=inventory,
            known_clues=known_clues,
            npc_states=npc_states,
        )
        return cls(
            scenario=scenario,
            state=state,
            prompt_repository=repo,
            preference_path=preference_path,
            registry_path=registry_path,
            options=options,
        )

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def has_pending_director_update(self) -> bool:
        with self._state_lock:
            return self._pending_director_future is not None

    def collect_director_update(self, *, wait: bool = False) -> DirectorOutput | None:
        with self._state_lock:
            if self._unread_director_result is not None:
                result = self._unread_director_result
                self._unread_director_result = None
                return result
            future = self._pending_director_future

        if future is None:
            return None

        if not wait and not future.done():
            return None

        if wait:
            future.result()

        result = self._consume_director_future(future)
        if result is not None:
            return result

        with self._state_lock:
            if self._unread_director_result is not None:
                result = self._unread_director_result
                self._unread_director_result = None
                return result
        return None

    def generate_opening(self) -> str:
        messages: list[Message] = [
            {"role": "system", "content": self.scenario.rule_excerpt(self.options.opening_rule_chars)},
            {"role": "assistant", "content": self.scenario.story_excerpt(self.options.opening_story_chars)},
            {"role": "user", "content": self.scenario.beginning_prompt},
        ]
        return get_normal_reply(
            input=messages,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=0.6,
        )

    def run_turn(
        self,
        player_text: str,
        *,
        background_director: bool = True,
    ) -> TurnResult:
        self.collect_director_update(wait=True)

        trace = self.trace_turn(
            player_text,
            include_next_director=not background_director,
        )
        with self._state_lock:
            self.state = trace.state_after_turn

        next_director_result: DirectorOutput | None = None
        pending_started = False
        if background_director:
            self._schedule_director_update(
                trace.next_director_context,
                expected_turn_id=trace.state_after_turn.meta.turn_id,
            )
            pending_started = True
        else:
            next_director_result = trace.next_director_result
            with self._state_lock:
                self.state = trace.state_after_next_director
                self._last_director_context = trace.next_director_context
                self._last_director_result = trace.next_director_result

        return TurnResult(
            action=trace.action,
            dicer_result=trace.dicer_result,
            npc_result=trace.npc_result,
            director_state_used=trace.director_state_used,
            narration=trace.narration,
            state=self.state,
            next_director_result=next_director_result,
            pending_director_started=pending_started,
        )

    def export_state_json(self) -> str:
        with self._state_lock:
            return self.state.model_dump_json(indent=2)

    def trace_turn(
        self,
        player_text: str,
        *,
        include_next_director: bool = True,
    ) -> TurnDebugTrace:
        with self._state_lock:
            state_before = self.state.model_copy(deep=True)
        action = parse_player_action(player_text)
        director_state_used = state_before.director.model_copy(deep=True)

        dicer_context = self.build_dicer_context(action, state_before)
        npc_context = self.build_npc_context(action, state_before)

        dicer_future = self._executor.submit(self._call_dicer_from_context, dicer_context)
        npc_future = self._executor.submit(self._call_npc_manager_from_context, npc_context)
        dicer_result = dicer_future.result()
        npc_result = npc_future.result()

        state_after_dicer = apply_delta(state_before, dicer_result.state_delta)
        state_after_npc = apply_delta(state_after_dicer, npc_result.state_delta)

        current_turn_events = self._collect_current_turn_events(action, dicer_result, npc_result)
        state_after_turn = append_recent_events(
            state_after_npc,
            current_turn_events,
            max_recent_events=self.options.max_recent_events,
        )
        updated_meta = advance_clock(state_after_turn.meta, self.options.minutes_per_turn)
        updated_meta = updated_meta.model_copy(update={"turn_id": state_after_turn.meta.turn_id + 1})
        state_after_turn = state_after_turn.model_copy(update={"meta": updated_meta})

        narrator_context = self.build_narrator_context(
            action=action,
            dicer_result=dicer_result,
            npc_result=npc_result,
            director_state=director_state_used,
            state=state_after_turn,
        )
        narration = self._call_narrator_from_context(narrator_context)

        next_director_context = self.build_next_director_context(
            action=action,
            dicer_result=dicer_result,
            npc_result=npc_result,
            narration=narration,
            state=state_after_turn,
        )
        next_director_result: DirectorOutput | None = None
        state_after_next_director: GameState | None = None
        if include_next_director:
            next_director_result = self._call_director_from_context(next_director_context)
            state_after_next_director = self._apply_director_result_to_state(
                state_after_turn,
                next_director_result,
            )

        return TurnDebugTrace(
            action=action,
            director_state_used=director_state_used,
            dicer_context=dicer_context,
            dicer_result=dicer_result,
            state_before_turn=state_before,
            state_after_dicer=state_after_dicer,
            npc_context=npc_context,
            npc_result=npc_result,
            state_after_npc=state_after_npc,
            narrator_context=narrator_context,
            narration=narration,
            state_after_turn=state_after_turn,
            next_director_context=next_director_context,
            next_director_result=next_director_result,
            state_after_next_director=state_after_next_director,
        )

    def build_dicer_context(
        self,
        action: ParsedPlayerAction,
        state: GameState | None = None,
    ) -> dict[str, object]:
        current_state = state or self.state
        return {
            "scenario_title": current_state.scenario_title,
            "rule_excerpt": self.scenario.rule_excerpt(self.options.dicer_rule_chars),
            "story_excerpt": self.scenario.story_excerpt(self.options.dicer_story_chars),
            "chapter_summary": current_state.chapter_summary,
            "recent_events": current_state.recent_events[-self.options.recent_event_window :],
            "current_time": format_game_time(current_state.meta),
            "player_state": current_state.player.model_dump(mode="python"),
            "scene_state": current_state.scene.model_dump(mode="python"),
            "player_action": action.model_dump(mode="python"),
        }

    def build_npc_context(
        self,
        action: ParsedPlayerAction,
        state: GameState,
    ) -> dict[str, object]:
        visible_names = state.scene.visible_npcs
        visible_npcs = [
            self._get_npc_state_payload(state, npc_name)
            for npc_name in visible_names
        ]
        background_npcs = [
            npc.model_dump(mode="python")
            for name, npc in state.npcs.items()
            if name not in set(visible_names)
            and (npc.current_goal or npc.task_summary or npc.last_public_status)
        ][: self.options.background_npc_window]

        return {
            "scenario_title": state.scenario_title,
            "rule_excerpt": self.scenario.rule_excerpt(self.options.npc_rule_chars),
            "story_excerpt": self.scenario.story_excerpt(self.options.npc_story_chars),
            "chapter_summary": state.chapter_summary,
            "recent_events": state.recent_events[-self.options.recent_event_window :],
            "current_time": format_game_time(state.meta),
            "player_action": action.model_dump(mode="python"),
            "player_state": state.player.model_dump(mode="python"),
            "scene_state": state.scene.model_dump(mode="python"),
            "visible_npcs": visible_npcs,
            "background_npcs": background_npcs,
        }

    def build_narrator_context(
        self,
        *,
        action: ParsedPlayerAction,
        dicer_result: DicerOutput,
        npc_result: NPCManagerOutput,
        director_state: DirectorState,
        state: GameState,
    ) -> dict[str, object]:
        return {
            "scenario_title": state.scenario_title,
            "rule_excerpt": self.scenario.rule_excerpt(self.options.narrator_rule_chars),
            "story_excerpt": self.scenario.story_excerpt(self.options.narrator_story_chars),
            "chapter_summary": state.chapter_summary,
            "current_time": format_game_time(state.meta),
            "player_action": action.model_dump(mode="python"),
            "scene_visible_state": {
                "location": state.scene.location,
                "description": state.scene.description,
                "visible_npcs": state.scene.visible_npcs,
                "interactive_objects": state.scene.interactive_objects,
                "hazards": state.scene.hazards,
                "noise_level": state.scene.noise_level,
            },
            "dicer_result": dicer_result.model_dump(mode="python"),
            "npc_result": npc_result.model_dump(mode="python"),
            "director_state": director_state.model_dump(mode="python"),
        }

    def build_next_director_context(
        self,
        *,
        action: ParsedPlayerAction,
        dicer_result: DicerOutput,
        npc_result: NPCManagerOutput,
        narration: str,
        state: GameState,
    ) -> dict[str, object]:
        return {
            "scenario_title": state.scenario_title,
            "rule_excerpt": self.scenario.rule_excerpt(self.options.director_rule_chars),
            "story_excerpt": self.scenario.story_excerpt(self.options.director_story_chars),
            "chapter_summary": state.chapter_summary,
            "recent_events": state.recent_events[-self.options.recent_event_window :],
            "current_time": format_game_time(state.meta),
            "player_action": action.model_dump(mode="python"),
            "scene_state": state.scene.model_dump(mode="python"),
            "player_state": state.player.model_dump(mode="python"),
            "npcs": {
                name: npc.model_dump(mode="python")
                for name, npc in state.npcs.items()
            },
            "director_state_before_update": state.director.model_dump(mode="python"),
            "dicer_result": dicer_result.model_dump(mode="python"),
            "npc_result": npc_result.model_dump(mode="python"),
            "narrator_output": narration,
        }

    def _call_dicer_from_context(self, context: dict[str, object]) -> DicerOutput:
        messages: list[Message] = [
            {"role": "system", "content": self.scenario.dicer_prompt},
            {
                "role": "system",
                "content": (
                    "你正在为一个状态驱动 TRPG 引擎工作。"
                    "请只依据提供的信息裁定，不要新增未给出的设定。"
                    "状态更新必须通过 state_delta 返回，若没有变化则返回空列表。"
                    "event_log_entries 只记录客观事实短句。"
                ),
            },
            {
                "role": "user",
                "content": "请根据以下 JSON 上下文完成本回合判定：\n"
                + json.dumps(context, ensure_ascii=False, indent=2),
            },
        ]

        return generate_structured_output(
            messages=messages,
            output_schema=DicerOutput,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=self.options.dicer_temperature,
        )

    def _call_npc_manager_from_context(self, context: dict[str, object]) -> NPCManagerOutput:
        if not context["visible_npcs"] and not context["background_npcs"]:
            return NPCManagerOutput()

        messages: list[Message] = [
            {"role": "system", "content": self.scenario.npc_manager_prompt},
            {
                "role": "system",
                "content": (
                    "你正在为一个状态驱动 TRPG 引擎工作。"
                    "本轮与 Dicer 并行执行，因此你只基于玩家意图、当前 NPC 状态、场景和近期历史做出反应。"
                    "不要等待 Dicer 的成功失败结果，不要代替 Dicer 做规则裁定，不要代替 Director 设计主线推进。"
                    "state_delta 只写 NPC 与场景相关的小幅状态变化。"
                    "event_log_entries 只记录客观事实短句。"
                ),
            },
            {
                "role": "user",
                "content": "请根据以下 JSON 上下文完成本轮 NPC 管理输出：\n"
                + json.dumps(context, ensure_ascii=False, indent=2),
            },
        ]

        return generate_structured_output(
            messages=messages,
            output_schema=NPCManagerOutput,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=self.options.npc_temperature,
        )

    def _call_director_from_context(self, context: dict[str, object]) -> DirectorOutput:
        messages: list[Message] = [
            {"role": "system", "content": self.scenario.director_prompt},
            {
                "role": "system",
                "content": (
                    "你正在为一个状态驱动 TRPG 引擎工作。"
                    "你的结果不会影响刚刚完成的 Narrator 输出，而是作为下一轮 Narrator 的参考。"
                    "请根据最新一轮 action、Dicer、NPC、Narrator 输出与历史，给出下一轮的节奏指导和宏观更新。"
                    "不要代替 Dicer 做规则裁定，不要代替 NPC Manager 生成具体台词。"
                    "state_delta 只写 Director 层面的标志、氛围、场景级变化。"
                    "event_log_entries 只记录客观事实短句。"
                ),
            },
            {
                "role": "user",
                "content": "请根据以下 JSON 上下文完成下一轮 Director 输出：\n"
                + json.dumps(context, ensure_ascii=False, indent=2),
            },
        ]

        return generate_structured_output(
            messages=messages,
            output_schema=DirectorOutput,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=self.options.director_temperature,
        )

    def _call_narrator_from_context(self, context: dict[str, object]) -> str:
        messages: list[Message] = [
            {"role": "system", "content": self.scenario.narrator_prompt},
            {
                "role": "system",
                "content": (
                    "当前引擎已启用 Dicer 与 NPC Manager。"
                    "Narrator 对本轮输出参考的是上一轮已完成的 director_state，而不是本轮刚生成的新 Director 结果。"
                    "你必须以 dicer_result 作为规则与结果基准，以 npc_result 呈现 NPC，"
                    "并将 director_state 隐性转译为叙事氛围或推进倾向。"
                    "不要擅自新增关键 NPC 行为、关键事件或主线推进。"
                ),
            },
            {
                "role": "user",
                "content": "请将以下 JSON 上下文转成玩家可见叙事：\n"
                + json.dumps(context, ensure_ascii=False, indent=2),
            },
        ]

        return get_normal_reply(
            input=messages,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=self.options.narrator_temperature,
        )

    def _schedule_director_update(
        self,
        context: dict[str, object],
        *,
        expected_turn_id: int,
    ) -> None:
        with self._state_lock:
            if self._pending_director_future is not None:
                raise RuntimeError("Cannot schedule a new director update while one is still pending")
            future = self._executor.submit(
                self._director_job,
                context,
                expected_turn_id,
            )
            self._pending_director_future = future
        future.add_done_callback(self._on_director_future_done)

    def _director_job(
        self,
        context: dict[str, object],
        expected_turn_id: int,
    ) -> tuple[dict[str, object], DirectorOutput, int]:
        return context, self._call_director_from_context(context), expected_turn_id

    def _on_director_future_done(
        self,
        future: Future[tuple[dict[str, object], DirectorOutput, int]],
    ) -> None:
        try:
            self._consume_director_future(future)
        except Exception:
            # Keep the future available for an explicit collect_director_update(wait=True),
            # which can surface the background failure to the caller.
            return

    def _consume_director_future(
        self,
        future: Future[tuple[dict[str, object], DirectorOutput, int]],
    ) -> DirectorOutput | None:
        context, director_result, expected_turn_id = future.result()
        with self._state_lock:
            if self._pending_director_future is not future:
                return None

            self._pending_director_future = None
            self._last_director_context = context
            self._last_director_result = director_result
            self._unread_director_result = director_result

            if self.state.meta.turn_id == expected_turn_id:
                self.state = self._apply_director_result_to_state(self.state, director_result)

            return director_result

    def _collect_current_turn_events(
        self,
        action: ParsedPlayerAction,
        dicer_result: DicerOutput,
        npc_result: NPCManagerOutput,
    ) -> list[str]:
        entries = list(dicer_result.event_log_entries)
        if not entries:
            entries.append(self._default_event_entry(action, dicer_result))
        entries.extend(entry for entry in npc_result.event_log_entries if entry)
        return entries

    def _apply_director_result_to_state(
        self,
        state: GameState,
        director_result: DirectorOutput,
    ) -> GameState:
        working_state = apply_delta(state, director_result.state_delta)
        payload = working_state.model_dump(mode="python")
        payload["director"]["pace_status"] = director_result.guidance.pace_status
        payload["director"]["current_tone"] = director_result.guidance.tone
        payload["director"]["pending_guidance"] = [
            *director_result.guidance.guidance,
            *director_result.guidance.foreshadow,
        ]
        payload["director"]["triggered_events"] = list(director_result.triggered_events)
        payload["director"]["endgame"] = director_result.guidance.endgame
        updated_state = GameState.model_validate(payload)

        director_events = list(director_result.event_log_entries)
        director_events.extend(
            f"Director触发:{event}"
            for event in director_result.triggered_events
            if event
        )
        if director_events:
            updated_state = append_recent_events(
                updated_state,
                director_events,
                max_recent_events=self.options.max_recent_events,
            )
        return updated_state

    @staticmethod
    def _default_event_entry(action: ParsedPlayerAction, dicer_result: DicerOutput) -> str:
        target = action.target or "目标未明"
        return (
            f"T{dicer_result.resolution.outcome}:{action.intent}:{target}:"
            f"{dicer_result.resolution.reason}"
        )

    @staticmethod
    def _get_npc_state_payload(state: GameState, npc_name: str) -> dict[str, object]:
        npc_state = state.npcs.get(npc_name)
        if npc_state is None:
            npc_state = NpcState(
                name=npc_name,
                location=state.scene.location,
                is_visible=True,
            )
        return npc_state.model_dump(mode="python")
