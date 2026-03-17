from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterator, Sequence

from text_model.function_TextGeneration import Message, get_normal_reply, get_stream_reply

from .models import (
    AgentRuntimeState,
    ConversationTurnRecord,
    CoreState,
    DicerOutput,
    DirectorOutput,
    DirectorState,
    EventRecord,
    GameMeta,
    GameState,
    LocationState,
    NPCManagerOutput,
    NpcState,
    ParsedPlayerAction,
    PlayerChoiceRecord,
    PlayerState,
    RuleCheckRecord,
    RuntimeLogEvent,
    ScenarioState,
    SceneState,
    TurnDebugTrace,
    TurnResult,
    TurnStreamEvent,
    build_rule_state,
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
    full_rule_text_for_agents: bool = True
    full_story_text_for_agents: bool = True
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
    dicer_dialogue_window: int = 2
    npc_dialogue_window: int = 3
    narrator_dialogue_window: int = 5
    director_dialogue_window: int = 5
    max_dialogue_window: int = 5
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


def _merge_nested_dicts(base: dict[str, object], patch: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in patch.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _merge_nested_dicts(base_value, value)
        else:
            merged[key] = value
    return merged


def _apply_state_overrides(state: GameState, overrides: dict[str, object] | None) -> GameState:
    if not overrides:
        return state
    payload = state.model_dump(mode="python")
    merged = _merge_nested_dicts(payload, overrides)
    return GameState.model_validate(merged)


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
    state_overrides: dict[str, object] | None = None,
) -> GameState:
    resolved_location = location or scenario.title
    registry = _build_initial_npc_registry(
        visible_npcs=visible_npcs,
        npc_states=npc_states,
        location=resolved_location,
    )
    resolved_scene_id = scene_id or "opening"
    resolved_location_id = resolved_scene_id

    initial_state = GameState(
        core=CoreState(
            meta=GameMeta(
                session_id=session_id or uuid.uuid4().hex[:12],
                rule_code=scenario.rule_code,
                story_code=scenario.story_code,
                scene_id=resolved_scene_id,
                chapter_id=resolved_scene_id,
            ),
            player=PlayerState(
                name=player_name,
                inventory=inventory or [],
                known_clues=known_clues or [],
                discovered_location_ids=[resolved_location_id],
            ),
            scene=SceneState(
                location_id=resolved_location_id,
                location=resolved_location,
                description=scene_description or scenario.opening_scene,
                visible_npcs=visible_npcs or [name for name, npc in registry.items() if npc.is_visible],
                interactive_objects=interactive_objects or [],
                hazards=hazards or [],
            ),
            npcs=registry,
            locations={
                resolved_location_id: LocationState(
                    location_id=resolved_location_id,
                    name=resolved_location,
                    description=scene_description or scenario.opening_scene,
                    discovered=True,
                    visible_features=list(interactive_objects or []),
                )
            },
            chapter_summary=scenario.story_summary,
        ),
        rule=build_rule_state(scenario.rule_code),
        scenario=ScenarioState(
            title=scenario.title,
            brief=scenario.story_summary,
            opening_scene=scenario.opening_scene,
        ),
        agent_runtime=AgentRuntimeState(director=DirectorState()),
    )
    return _apply_state_overrides(initial_state, state_overrides)


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
        state_override_path: str | None = None,
    ) -> "MinimalTRPGEngine":
        repo = prompt_repository or PromptRepository()
        scenario = repo.load_scenario(rule_code=rule_code, story_code=story_code)
        state_overrides = repo.load_state_overrides(
            rule_code=rule_code,
            story_code=story_code,
            scenario=scenario,
            preference_path=preference_path,
            registry_path=registry_path,
            extra_override_path=state_override_path,
        )
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
            state_overrides=state_overrides,
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

    @staticmethod
    def _build_log_event(
        *,
        phase: str,
        stage: str,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> RuntimeLogEvent:
        return RuntimeLogEvent(
            phase=phase,
            stage=stage,
            message=message,
            payload=payload or {},
        )

    @classmethod
    def _emit_runtime_log(
        cls,
        *,
        progress_callback: Callable[[str], None] | None = None,
        event_callback: Callable[[RuntimeLogEvent], None] | None = None,
        phase: str,
        stage: str,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> RuntimeLogEvent:
        event = cls._build_log_event(
            phase=phase,
            stage=stage,
            message=message,
            payload=payload,
        )
        if progress_callback is not None:
            progress_callback(message)
        if event_callback is not None:
            event_callback(event)
        return event

    def generate_opening(
        self,
        *,
        progress_callback: Callable[[str], None] | None = None,
        event_callback: Callable[[RuntimeLogEvent], None] | None = None,
    ) -> str:
        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="opening",
            stage="build_context",
            message="[Opening] Building opening prompt context...",
            payload={
                "rule_code": self.scenario.rule_code,
                "story_code": self.scenario.story_code,
            },
        )
        messages: list[Message] = [
            {"role": "system", "content": self.scenario.rule_excerpt(self.options.opening_rule_chars)},
            {"role": "assistant", "content": self.scenario.story_excerpt(self.options.opening_story_chars)},
            {"role": "user", "content": self.scenario.beginning_prompt},
        ]

        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="opening",
            stage="call_model",
            message="[Opening] Generating opening narration...",
        )
        opening = get_normal_reply(
            input=messages,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=0.6,
        )
        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="opening",
            stage="complete",
            message="[Opening] Opening narration ready.",
            payload={"text_length": len(opening)},
        )
        return opening

    @staticmethod
    def _emit_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    @staticmethod
    def _normalize_game_state_instance(state: GameState | object | None) -> GameState | None:
        if state is None:
            return None
        if isinstance(state, GameState):
            return state
        if hasattr(state, "model_dump"):
            return GameState.model_validate(state.model_dump(mode="python"))
        return GameState.model_validate(state)

    def run_turn(
        self,
        player_text: str,
        *,
        background_director: bool = True,
        progress_callback: Callable[[str], None] | None = None,
        event_callback: Callable[[RuntimeLogEvent], None] | None = None,
    ) -> TurnResult:
        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="wait_director",
            message="[Turn] Waiting for pending director update...",
        )
        self.collect_director_update(wait=True)

        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="start",
            message="[Turn] Starting turn pipeline...",
            payload={"background_director": background_director},
        )
        trace = self.trace_turn(
            player_text,
            include_next_director=not background_director,
            progress_callback=progress_callback,
            event_callback=event_callback,
        )
        with self._state_lock:
            self.state = trace.state_after_turn

        next_director_result: DirectorOutput | None = None
        pending_started = False
        if background_director:
            self._emit_runtime_log(
                progress_callback=progress_callback,
                event_callback=event_callback,
                phase="turn",
                stage="schedule_director",
                message="[Turn] Scheduling next director update in background...",
            )
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

        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="complete",
            message="[Turn] Turn complete.",
            payload={"turn_id": self.state.meta.turn_id},
        )

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

    def stream_turn(
        self,
        player_text: str,
        *,
        background_director: bool = True,
    ) -> Iterator[TurnStreamEvent]:
        self.collect_director_update(wait=True)

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
        state_after_turn = state_after_turn.model_copy(
            update={
                "core": state_after_turn.core.model_copy(update={"meta": updated_meta}),
                "agent_runtime": state_after_turn.agent_runtime.model_copy(
                    update={"last_player_action_text": player_text}
                ),
            }
        )

        narrator_context = self.build_narrator_context(
            action=action,
            dicer_result=dicer_result,
            npc_result=npc_result,
            director_state=director_state_used,
            state=state_after_turn,
        )

        narration_parts: list[str] = []
        for chunk in self._stream_narrator_from_context(narrator_context):
            if not chunk:
                continue
            narration_parts.append(chunk)
            yield TurnStreamEvent(event="narration_chunk", delta=chunk)

        narration = "".join(narration_parts)
        state_after_turn = state_after_turn.model_copy(
            update={
                "agent_runtime": state_after_turn.agent_runtime.model_copy(
                    update={"last_narration": narration}
                )
            }
        )
        state_after_turn = self._record_turn_artifacts(
            state_after_turn,
            action=action,
            narration=narration,
            dicer_result=dicer_result,
            npc_result=npc_result,
            current_turn_events=current_turn_events,
            max_dialogue_window=self.options.max_dialogue_window,
        )

        next_director_context = self.build_next_director_context(
            action=action,
            dicer_result=dicer_result,
            npc_result=npc_result,
            narration=narration,
            state=state_after_turn,
        )

        next_director_result: DirectorOutput | None = None
        pending_started = False
        final_state = state_after_turn
        with self._state_lock:
            self.state = state_after_turn

        if background_director:
            self._schedule_director_update(
                next_director_context,
                expected_turn_id=state_after_turn.meta.turn_id,
            )
            pending_started = True
        else:
            next_director_result = self._call_director_from_context(next_director_context)
            final_state = self._apply_director_result_to_state(state_after_turn, next_director_result)
            with self._state_lock:
                self.state = final_state
                self._last_director_context = next_director_context
                self._last_director_result = next_director_result

        yield TurnStreamEvent(
            event="turn_result",
            result=TurnResult(
                action=action,
                dicer_result=dicer_result,
                npc_result=npc_result,
                director_state_used=director_state_used,
                narration=narration,
                state=final_state,
                next_director_result=next_director_result,
                pending_director_started=pending_started,
            ),
        )

    def export_state_json(self) -> str:
        with self._state_lock:
            return self.state.model_dump_json(indent=2)

    def trace_turn(
        self,
        player_text: str,
        *,
        include_next_director: bool = True,
        progress_callback: Callable[[str], None] | None = None,
        event_callback: Callable[[RuntimeLogEvent], None] | None = None,
    ) -> TurnDebugTrace:
        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="parse_action",
            message="[Turn] Parsing player action...",
        )
        with self._state_lock:
            state_before = self.state.model_copy(deep=True)
        action = parse_player_action(player_text)
        director_state_used = state_before.director.model_copy(deep=True)

        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="build_contexts",
            message="[Turn] Building Dicer and NPC Manager contexts...",
        )
        dicer_context = self.build_dicer_context(action, state_before)
        npc_context = self.build_npc_context(action, state_before)

        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="run_dicer_npc",
            message="[Turn] Running Dicer and NPC Manager...",
        )
        dicer_future = self._executor.submit(self._call_dicer_from_context, dicer_context)
        npc_future = self._executor.submit(self._call_npc_manager_from_context, npc_context)
        dicer_result = dicer_future.result()
        npc_result = npc_future.result()

        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="apply_deltas",
            message="[Turn] Applying state deltas...",
        )
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
        state_after_turn = state_after_turn.model_copy(
            update={
                "core": state_after_turn.core.model_copy(update={"meta": updated_meta}),
                "agent_runtime": state_after_turn.agent_runtime.model_copy(
                    update={
                        "last_player_action_text": player_text,
                    }
                ),
            }
        )

        narrator_context = self.build_narrator_context(
            action=action,
            dicer_result=dicer_result,
            npc_result=npc_result,
            director_state=director_state_used,
            state=state_after_turn,
        )
        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="turn",
            stage="run_narrator",
            message="[Turn] Running Narrator...",
        )
        narration = self._call_narrator_from_context(narrator_context)
        state_after_turn = state_after_turn.model_copy(
            update={
                "agent_runtime": state_after_turn.agent_runtime.model_copy(
                    update={"last_narration": narration}
                )
            }
        )
        state_after_turn = self._record_turn_artifacts(
            state_after_turn,
            action=action,
            narration=narration,
            dicer_result=dicer_result,
            npc_result=npc_result,
            current_turn_events=current_turn_events,
            max_dialogue_window=self.options.max_dialogue_window,
        )

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
            self._emit_runtime_log(
                progress_callback=progress_callback,
                event_callback=event_callback,
                phase="turn",
                stage="run_director",
                message="[Turn] Running Director...",
            )
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
            state_before_turn=self._normalize_game_state_instance(state_before),
            state_after_dicer=self._normalize_game_state_instance(state_after_dicer),
            npc_context=npc_context,
            npc_result=npc_result,
            state_after_npc=self._normalize_game_state_instance(state_after_npc),
            narrator_context=narrator_context,
            narration=narration,
            state_after_turn=self._normalize_game_state_instance(state_after_turn),
            next_director_context=next_director_context,
            next_director_result=next_director_result,
            state_after_next_director=self._normalize_game_state_instance(state_after_next_director),
        )

    @staticmethod
    def _serialize_dialogue_window(state: GameState, limit: int) -> list[dict[str, object]]:
        records = state.agent_runtime.dialogue_window[-limit:] if limit > 0 else []
        return [record.model_dump(mode="python") for record in records]

    @staticmethod
    def _serialize_recent_choice_window(state: GameState, limit: int = 5) -> list[dict[str, object]]:
        records = state.core.player_choices[-limit:] if limit > 0 else []
        return [record.model_dump(mode="python") for record in records]

    @staticmethod
    def _serialize_recent_rule_checks(state: GameState, limit: int = 5) -> list[dict[str, object]]:
        records = state.rule.check_history[-limit:] if limit > 0 else []
        return [record.model_dump(mode="python") for record in records]

    def _build_history_view(
        self,
        state: GameState,
        *,
        dialogue_limit: int,
        include_choices: bool = False,
        include_rule_checks: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chapter_summary": state.chapter_summary,
            "recent_events": state.recent_events[-self.options.recent_event_window :],
            "recent_dialogue": self._serialize_dialogue_window(state, dialogue_limit),
        }
        if include_choices:
            payload["recent_player_choices"] = self._serialize_recent_choice_window(state)
        if include_rule_checks:
            payload["recent_rule_checks"] = self._serialize_recent_rule_checks(state)
        return payload

    def _rule_reference(self, max_chars: int) -> str:
        if self.options.full_rule_text_for_agents:
            return self.scenario.rule_text
        return self.scenario.rule_excerpt(max_chars)

    def _story_reference(self, max_chars: int) -> str:
        if self.options.full_story_text_for_agents:
            return self.scenario.story_text
        return self.scenario.story_excerpt(max_chars)

    def _build_dicer_state_focus(self, state: GameState) -> dict[str, object]:
        visible_names = set(state.scene.visible_npcs)
        visible_npcs = {
            name: npc.model_dump(mode="python")
            for name, npc in state.npcs.items()
            if name in visible_names
        }
        return {
            "meta": state.meta.model_dump(mode="python"),
            "player": state.player.model_dump(mode="python"),
            "scene": state.scene.model_dump(mode="python"),
            "visible_npcs": visible_npcs,
            "player_known_clues": {
                clue_id: clue.model_dump(mode="python")
                for clue_id, clue in state.core.clues.items()
                if clue.player_known
            },
            "recent_rule_checks": self._serialize_recent_rule_checks(state),
        }

    def _build_npc_state_focus(self, state: GameState) -> dict[str, object]:
        visible_names = list(state.scene.visible_npcs)
        visible_npcs = {
            name: self._get_npc_state_payload(state, name)
            for name in visible_names
        }
        background_npcs = {
            name: npc.model_dump(mode="python")
            for name, npc in state.npcs.items()
            if name not in set(visible_names) and (npc.current_goal or npc.task_summary or npc.last_public_status)
        }
        return {
            "meta": state.meta.model_dump(mode="python"),
            "player": state.player.model_dump(mode="python"),
            "scene": state.scene.model_dump(mode="python"),
            "visible_npcs": visible_npcs,
            "background_npcs": background_npcs,
            "player_known_clues": {
                clue_id: clue.model_dump(mode="python")
                for clue_id, clue in state.core.clues.items()
                if clue.player_known
            },
            "active_locations": {
                location_id: location.model_dump(mode="python")
                for location_id, location in state.core.locations.items()
                if location.discovered or location_id == state.scene.location_id
            },
        }

    def _build_narrator_state_focus(self, state: GameState) -> dict[str, object]:
        visible_names = list(state.scene.visible_npcs)
        return {
            "meta": state.meta.model_dump(mode="python"),
            "player_public_state": {
                "name": state.player.name,
                "status": list(state.player.status),
                "inventory": list(state.player.inventory),
                "known_clues": list(state.player.known_clues),
                "short_term_goals": list(state.player.short_term_goals),
                "relationship_notes": list(state.player.relationship_notes),
            },
            "scene_visible_state": {
                "location": state.scene.location,
                "description": state.scene.description,
                "visible_npcs": visible_names,
                "interactive_objects": list(state.scene.interactive_objects),
                "hazards": list(state.scene.hazards),
                "noise_level": state.scene.noise_level,
                "exits": list(state.scene.exits),
                "deadline_hints": list(state.scene.deadline_hints),
            },
            "visible_npc_cards": {
                name: self._get_npc_state_payload(state, name)
                for name in visible_names
            },
            "player_known_clues": {
                clue_id: clue.model_dump(mode="python")
                for clue_id, clue in state.core.clues.items()
                if clue.player_known
            },
            "active_objectives": {
                objective_id: objective.model_dump(mode="python")
                for objective_id, objective in state.scenario.objectives.items()
                if objective.status in {"active", "locked"}
            },
        }

    def _build_director_state_focus(self, state: GameState) -> dict[str, object]:
        return {
            "meta": state.meta.model_dump(mode="python"),
            "player": state.player.model_dump(mode="python"),
            "scene": state.scene.model_dump(mode="python"),
            "npcs": {
                name: npc.model_dump(mode="python")
                for name, npc in state.npcs.items()
            },
            "locations": {
                location_id: location.model_dump(mode="python")
                for location_id, location in state.core.locations.items()
            },
            "player_known_clues": {
                clue_id: clue.model_dump(mode="python")
                for clue_id, clue in state.core.clues.items()
                if clue.player_known
            },
            "scenario_progress": {
                "current_arc": state.scenario.current_arc,
                "current_stage": state.scenario.current_stage,
                "objectives": {
                    objective_id: objective.model_dump(mode="python")
                    for objective_id, objective in state.scenario.objectives.items()
                },
                "triggers": {
                    trigger_id: trigger.model_dump(mode="python")
                    for trigger_id, trigger in state.scenario.triggers.items()
                },
                "world_facts": list(state.scenario.world_facts),
                "unresolved_questions": list(state.scenario.unresolved_questions),
                "ending_candidates": list(state.scenario.ending_candidates),
            },
            "recent_rule_checks": self._serialize_recent_rule_checks(state),
            "recent_player_choices": self._serialize_recent_choice_window(state),
            "director_runtime": state.director.model_dump(mode="python"),
            "npc_manager_notes": list(state.agent_runtime.npc_manager_notes[-8:]),
            "last_narration": state.agent_runtime.last_narration,
        }

    def build_dicer_context(
        self,
        action: ParsedPlayerAction,
        state: GameState | None = None,
    ) -> dict[str, object]:
        current_state = state or self.state
        return {
            "scenario_title": current_state.scenario_title,
            "rule_reference": self._rule_reference(self.options.dicer_rule_chars),
            "story_reference": self._story_reference(self.options.dicer_story_chars),
            "core_state": current_state.core.model_dump(mode="python"),
            "rule_state": current_state.rule.model_dump(mode="python"),
            "scenario_state": current_state.scenario.model_dump(mode="python"),
            "history_view": self._build_history_view(
                current_state,
                dialogue_limit=self.options.dicer_dialogue_window,
                include_rule_checks=True,
            ),
            "state_focus": self._build_dicer_state_focus(current_state),
            "current_time": format_game_time(current_state.meta),
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
            "rule_reference": self._rule_reference(self.options.npc_rule_chars),
            "story_reference": self._story_reference(self.options.npc_story_chars),
            "core_state": state.core.model_dump(mode="python"),
            "rule_state": state.rule.model_dump(mode="python"),
            "scenario_state": state.scenario.model_dump(mode="python"),
            "history_view": self._build_history_view(
                state,
                dialogue_limit=self.options.npc_dialogue_window,
                include_choices=True,
            ),
            "state_focus": self._build_npc_state_focus(state),
            "current_time": format_game_time(state.meta),
            "player_action": action.model_dump(mode="python"),
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
            "rule_reference": self._rule_reference(self.options.narrator_rule_chars),
            "story_reference": self._story_reference(self.options.narrator_story_chars),
            "core_state": state.core.model_dump(mode="python"),
            "rule_state": state.rule.model_dump(mode="python"),
            "scenario_state": state.scenario.model_dump(mode="python"),
            "history_view": self._build_history_view(
                state,
                dialogue_limit=self.options.narrator_dialogue_window,
                include_choices=True,
            ),
            "state_focus": self._build_narrator_state_focus(state),
            "current_time": format_game_time(state.meta),
            "player_action": action.model_dump(mode="python"),
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
            "rule_reference": self._rule_reference(self.options.director_rule_chars),
            "story_reference": self._story_reference(self.options.director_story_chars),
            "core_state": state.core.model_dump(mode="python"),
            "rule_state": state.rule.model_dump(mode="python"),
            "scenario_state": state.scenario.model_dump(mode="python"),
            "agent_runtime_state": state.agent_runtime.model_dump(mode="python"),
            "history_view": self._build_history_view(
                state,
                dialogue_limit=self.options.director_dialogue_window,
                include_choices=True,
                include_rule_checks=True,
            ),
            "state_focus": self._build_director_state_focus(state),
            "current_time": format_game_time(state.meta),
            "player_action": action.model_dump(mode="python"),
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
                    "rule_reference、story_reference、rule_state、scenario_state 是稳定真相源；"
                    "history_view 是压缩历史；state_focus 是本轮最相关状态切片。"
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
                    "rule_reference、story_reference、rule_state、scenario_state 是稳定真相源；"
                    "history_view 是压缩历史；state_focus、visible_npcs、background_npcs 是本轮重点。"
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
                    "rule_reference、story_reference、rule_state、scenario_state 是稳定真相源；"
                    "history_view 是压缩历史；state_focus 提供导演层重点视图。"
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

    def _build_narrator_messages(self, context: dict[str, object]) -> list[Message]:
        return [
            {"role": "system", "content": self.scenario.narrator_prompt},
            {
                "role": "system",
                "content": (
                    "当前引擎已启用 Dicer 与 NPC Manager。"
                    "Narrator 对本轮输出参考的是上一轮已完成的 director_state，而不是本轮刚生成的新 Director 结果。"
                    "你必须以 dicer_result 作为规则与结果基准，以 npc_result 呈现 NPC，"
                    "并将 director_state 隐性转译为叙事氛围或推进倾向。"
                    "rule_reference、story_reference、rule_state、scenario_state 是稳定真相源；"
                    "history_view 是压缩历史；state_focus 提供玩家可见重点状态。"
                    "不要擅自新增关键 NPC 行为、关键事件或主线推进。"
                ),
            },
            {
                "role": "user",
                "content": "请将以下 JSON 上下文转成玩家可见叙事：\n"
                + json.dumps(context, ensure_ascii=False, indent=2),
            },
        ]

    def _call_narrator_from_context(self, context: dict[str, object]) -> str:
        messages = self._build_narrator_messages(context)

        return get_normal_reply(
            input=messages,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=self.options.narrator_temperature,
        )

    def _stream_narrator_from_context(self, context: dict[str, object]) -> Iterator[str]:
        messages = self._build_narrator_messages(context)
        return get_stream_reply(
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

    @staticmethod
    def _record_turn_artifacts(
        state: GameState,
        *,
        action: ParsedPlayerAction,
        narration: str,
        dicer_result: DicerOutput,
        npc_result: NPCManagerOutput,
        current_turn_events: list[str],
        max_dialogue_window: int,
    ) -> GameState:
        committed_turn_id = max(state.meta.turn_id - 1, 0)
        choice_record = PlayerChoiceRecord(
            turn_id=committed_turn_id,
            action_text=action.raw_text,
            summary=f"{action.intent}:{action.target or 'unspecified'}",
            tags=list(action.tags),
        )
        event_records = list(state.core.event_records)
        event_records.extend(
            EventRecord(turn_id=committed_turn_id, source="turn", summary=entry)
            for entry in current_turn_events
            if entry
        )
        check_history = list(state.rule.check_history)
        check_history.append(
            RuleCheckRecord(
                turn_id=committed_turn_id,
                action_summary=action.raw_text,
                outcome=dicer_result.resolution.outcome,
                consequence=dicer_result.resolution.consequence,
                rule_refs=list(dicer_result.validity.rule_refs),
            )
        )
        dialogue_window = list(state.agent_runtime.dialogue_window)
        dialogue_window.append(
            ConversationTurnRecord(
                turn_id=committed_turn_id,
                player_text=action.raw_text,
                narrator_text=narration,
                action_summary=f"{action.intent}:{action.target or 'unspecified'}",
                outcome=dicer_result.resolution.outcome,
            )
        )
        npc_notes = list(state.agent_runtime.npc_manager_notes)
        npc_notes.extend(
            update.progress
            for update in npc_result.background_updates
            if update.progress
        )
        return state.model_copy(
            update={
                "core": state.core.model_copy(
                    update={
                        "player_choices": [*state.core.player_choices, choice_record],
                        "event_records": event_records,
                    }
                ),
                "rule": state.rule.model_copy(update={"check_history": check_history}),
                "agent_runtime": state.agent_runtime.model_copy(
                    update={
                        "dialogue_window": dialogue_window[-max_dialogue_window:],
                        "npc_manager_notes": npc_notes[-12:],
                    }
                ),
            }
        )

    def _apply_director_result_to_state(
        self,
        state: GameState,
        director_result: DirectorOutput,
    ) -> GameState:
        working_state = apply_delta(state, director_result.state_delta)
        payload = working_state.model_dump(mode="python")
        payload["agent_runtime"]["director"]["pace_status"] = director_result.guidance.pace_status
        payload["agent_runtime"]["director"]["current_tone"] = director_result.guidance.tone
        payload["agent_runtime"]["director"]["pending_guidance"] = [
            *director_result.guidance.guidance,
            *director_result.guidance.foreshadow,
        ]
        payload["agent_runtime"]["director"]["triggered_events"] = list(director_result.triggered_events)
        payload["agent_runtime"]["director"]["endgame"] = director_result.guidance.endgame
        payload["scenario"]["foreshadow_queue"] = list(director_result.guidance.foreshadow)
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
