from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator, Sequence

import data.data_Path as data_path
from text_model.function_TextGeneration import Message, get_normal_reply, get_stream_reply
from tools.function_Preference import load_merged_preferences

from .language_support import (
    build_output_language_instruction,
    get_action_parser_path,
    get_localized_agent_note,
    get_narrator_language_note,
    get_opening_language_note,
    normalize_difficulty_code,
    normalize_language_code,
)
from .models import (
    AgentRuntimeState,
    ConversationTurnRecord,
    CoreState,
    DeltaOperation,
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

MARKDOWN_HEADING_PREFIX = re.compile(r"^\s{0,3}#{2,6}\s*")
MARKDOWN_BOLD_TOKEN = re.compile(r"\*\*")
HEADING_NUMBER_PREFIX = re.compile(r"^(?:NPC\d+|[一二三四五六七八九十]+|\d+)[\.、:\s-]*")
NPC_SECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^([^：:]{1,16})[：:]\s*([^\(（]{1,24})(?:[\(（](.+?)[\)）])?$"),
    re.compile(r"^(?:\d+[\.\s、]*)?([^\(（]{2,24})(?:[\(（](.+?)[\)）])?$"),
]
VISIBLE_NPC_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"([\u4e00-\u9fffA-Za-z·]{2,12}探长)"),
    re.compile(r"([\u4e00-\u9fffA-Za-z·]{2,12}村长)"),
    re.compile(r"([\u4e00-\u9fffA-Za-z·]{2,12}管家)"),
    re.compile(r"([\u4e00-\u9fffA-Za-z·]{2,12}僧人)"),
]


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


CODE_ROOT = Path(__file__).resolve().parents[1]
ACTION_TARGET_STOP_CHARS = "，。；,.！？!?\n\r\t"
ACTION_TARGET_MAX_LEN = 32


def _resolve_code_path(path_str: str) -> Path:
    return CODE_ROOT / Path(path_str)


def _normalize_language_code(code: str | None) -> str:
    if not code:
        return ""
    return code.strip().casefold()


@lru_cache(maxsize=4)
def _load_action_parser_data(config_path: str | None = None) -> dict[str, object]:
    config_file: Path | None = None
    if config_path:
        config_file = Path(config_path)
    if config_file is None:
        config_file = get_action_parser_path(None)
    if config_file is None:
        config_file = _resolve_code_path(data_path.PATH_DATA_ACTION_PARSER)
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Action parser config root must be a dict")
    return payload


def _get_preferred_language_code() -> str:
    pref_file = _resolve_code_path(data_path.PATH_DATA_PREFERENCE)
    default_pref_file = _resolve_code_path(data_path.PATH_DATA_DEFAULT_PREFERENCE)
    prefs = load_merged_preferences(
        init_path=str(default_pref_file),
        path=str(pref_file),
    )
    language = prefs.get("language", "")
    return str(language).strip() if language else ""


def _normalize_keyword_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            values.append(normalized)
    return values


def _build_language_lookup(payload: dict[str, object]) -> tuple[str, dict[str, dict[str, object]]]:
    default_language = _normalize_language_code(str(payload.get("default_language", "zh-CN")))
    raw_languages = payload.get("languages", {})
    if not isinstance(raw_languages, dict) or not raw_languages:
        raise TypeError("Action parser config languages must be a non-empty dict")

    languages: dict[str, dict[str, object]] = {}
    for raw_code, raw_config in raw_languages.items():
        if isinstance(raw_config, dict):
            languages[_normalize_language_code(str(raw_code))] = raw_config
    if not languages:
        raise TypeError("Action parser config languages must contain object values")
    return default_language, languages


def _resolve_language_variants(language_code: str, available_codes: set[str], default_code: str) -> list[str]:
    variants: list[str] = []
    normalized = _normalize_language_code(language_code)
    if normalized:
        variants.append(normalized)
        base = normalized.split("-", 1)[0]
        if base != normalized:
            variants.append(base)

    variants.extend(["zh-cn", "zh", default_code])

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in variants:
        if candidate in seen or candidate not in available_codes:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _get_action_parser_config(
    *,
    config_path: str | None = None,
    language_code: str | None = None,
) -> dict[str, object]:
    resolved_config_path = config_path
    if resolved_config_path is None:
        path = get_action_parser_path(language_code)
        resolved_config_path = str(path) if path is not None else None
    payload = _load_action_parser_data(resolved_config_path)
    default_language, languages = _build_language_lookup(payload)
    preferred_language = language_code or _get_preferred_language_code()
    selected_codes = _resolve_language_variants(preferred_language, set(languages.keys()), default_language)

    intents: list[tuple[str, list[str]]] = []
    target_verbs: list[str] = []
    tags: dict[str, list[str]] = {}

    for code in selected_codes:
        config = languages.get(code, {})
        raw_intents = config.get("intents", {})
        if isinstance(raw_intents, dict):
            for intent_name, keywords in raw_intents.items():
                normalized_keywords = _normalize_keyword_list(keywords)
                if normalized_keywords:
                    intents.append((str(intent_name), normalized_keywords))

        target_verbs.extend(_normalize_keyword_list(config.get("target_verbs", [])))

        raw_tags = config.get("tags", {})
        if isinstance(raw_tags, dict):
            for tag_name, keywords in raw_tags.items():
                tags.setdefault(str(tag_name), []).extend(_normalize_keyword_list(keywords))

    return {
        "intents": intents,
        "target_verbs": list(dict.fromkeys(target_verbs)),
        "tags": {
            tag_name: list(dict.fromkeys(values))
            for tag_name, values in tags.items()
        },
    }


def _contains_keyword(text: str, lowered_text: str, keyword: str) -> bool:
    if not keyword:
        return False
    if keyword.isascii():
        return keyword.casefold() in lowered_text
    return keyword in text


def _extract_action_target(
    text: str,
    *,
    lowered_normalized: str,
    target_verbs: Sequence[str],
) -> str | None:
    match_index = None
    match_verb = ""
    for verb in target_verbs:
        haystack = lowered_normalized if verb.isascii() else text
        needle = verb.casefold() if verb.isascii() else verb
        start_index = haystack.find(needle)
        if start_index == -1:
            continue
        if match_index is None or start_index < match_index or (
            start_index == match_index and len(verb) > len(match_verb)
        ):
            match_index = start_index
            match_verb = verb

    if match_index is None:
        return None

    after_target = _extract_target_after_verb(text, match_index + len(match_verb))
    if after_target:
        return after_target

    before_target = _extract_target_before_verb(text, match_index)
    if before_target:
        return before_target
    return None


def _extract_target_after_verb(text: str, start_index: int) -> str | None:
    remainder = text[start_index:].lstrip(" ：:，,")
    if not remainder:
        return None

    chars: list[str] = []
    for char in remainder:
        if char in ACTION_TARGET_STOP_CHARS:
            break
        chars.append(char)
        if len(chars) >= ACTION_TARGET_MAX_LEN:
            break

    target = "".join(chars).strip()
    return target or None


def _extract_target_before_verb(text: str, verb_index: int) -> str | None:
    if verb_index <= 0:
        return None

    segment = text[max(0, verb_index - ACTION_TARGET_MAX_LEN):verb_index]
    for separator in ("。", "！", "？", ".", "!", "?", "，", ",", "然后", "并且", "并", "and "):
        if separator in segment:
            segment = segment.split(separator)[-1]

    target = segment.strip(" ：:，,。；;!！？")
    for suffix in ("を", "へ", "に", "が", "の", "里", "上", "下"):
        if target.endswith(suffix) and len(target) > len(suffix):
            target = target[: -len(suffix)].strip()
            break
    for prefix in ("我想", "我先", "我", "先", "想要", "想", "まず", "私は", "ぼくは", "俺は", "I ", "I softly "):
        if target.startswith(prefix):
            target = target[len(prefix):].strip()

    return target or None


def parse_player_action(
    player_text: str,
    *,
    config_path: str | None = None,
    language_code: str | None = None,
) -> ParsedPlayerAction:
    normalized = " ".join(player_text.split())
    lowered_normalized = normalized.casefold()
    parser_config = _get_action_parser_config(
        config_path=config_path,
        language_code=language_code,
    )

    intent = "general"
    for candidate_intent, keywords in parser_config["intents"]:
        if any(_contains_keyword(normalized, lowered_normalized, keyword) for keyword in keywords):
            intent = candidate_intent
            break

    target = _extract_action_target(
        normalized,
        lowered_normalized=lowered_normalized,
        target_verbs=parser_config["target_verbs"],
    )

    tags: list[str] = []
    for tag_name, keywords in parser_config["tags"].items():
        if any(_contains_keyword(normalized, lowered_normalized, keyword) for keyword in keywords):
            tags.append(tag_name)

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


def _normalize_initial_state(state: GameState) -> GameState:
    scene_visible_names = [name.strip() for name in state.scene.visible_npcs if name and name.strip()]
    deduped_visible_names = list(dict.fromkeys(scene_visible_names))
    registry = dict(state.npcs)

    for npc_name, npc_state in list(registry.items()):
        if npc_state.is_visible and npc_name not in deduped_visible_names:
            deduped_visible_names.append(npc_name)

    for npc_name in deduped_visible_names:
        npc_state = registry.get(npc_name)
        if npc_state is None:
            registry[npc_name] = NpcState(
                name=npc_name,
                location=state.scene.location,
                location_id=state.scene.location_id,
                is_visible=True,
            )
            continue
        registry[npc_name] = npc_state.model_copy(
            update={
                "is_visible": True,
                "location": npc_state.location or state.scene.location,
                "location_id": npc_state.location_id or state.scene.location_id,
            }
        )

    return state.model_copy(
        update={
            "core": state.core.model_copy(
                update={
                    "scene": state.scene.model_copy(update={"visible_npcs": deduped_visible_names}),
                    "npcs": registry,
                }
            )
        }
    )


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
    return _normalize_initial_state(_apply_state_overrides(initial_state, state_overrides))


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
        language_code: str = "zh-CN",
        difficulty_code: str = "easy",
    ) -> None:
        self.scenario = scenario
        self.state = self._seed_story_npcs_into_state(state)
        self.prompt_repository = prompt_repository or PromptRepository()
        self.preference_path = preference_path
        self.registry_path = registry_path
        self.options = options
        self.language_code = normalize_language_code(language_code)
        self.difficulty_code = normalize_difficulty_code(difficulty_code)
        self._state_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=self.options.max_parallel_workers)
        self._pending_director_future: Future[tuple[dict[str, object], DirectorOutput, int]] | None = None
        self._last_director_context: dict[str, object] | None = None
        self._last_director_result: DirectorOutput | None = None
        self._unread_director_result: DirectorOutput | None = None
        self._opening_npc_result: NPCManagerOutput | None = None
        self._opening_npc_initialized = False

    @staticmethod
    def _clean_heading_text(line: str) -> str:
        text = MARKDOWN_HEADING_PREFIX.sub("", line).strip()
        text = MARKDOWN_BOLD_TOKEN.sub("", text).strip()
        return HEADING_NUMBER_PREFIX.sub("", text).strip()

    @staticmethod
    def _build_story_npc_payload(name: str, *, identity: str = "", detail: str = "", role_hint: str = "") -> dict[str, object]:
        description_parts = [part for part in (identity, detail) if part]
        return {
            "name": name,
            "identity": identity or role_hint,
            "description": "；".join(description_parts)[:220],
            "is_core_npc": True,
            "location": "",
            "outfit": "",
            "demeanor": "",
            "current_mood": "",
            "physical_status": "",
            "expression": "",
            "current_goal": "",
            "task_summary": "",
            "last_public_status": "",
            "is_visible": False,
            "tags": ["story_seeded"],
        }

    def _extract_story_npc_candidates(self) -> dict[str, dict[str, object]]:
        candidates: dict[str, dict[str, object]] = {}
        lines = self.scenario.story_text.splitlines()
        in_npc_section = False

        for index, raw_line in enumerate(lines):
            stripped_line = raw_line.strip()
            cleaned = self._clean_heading_text(raw_line)
            if not cleaned:
                continue

            if any(token in stripped_line for token in ("关键NPC", "NPC 卡", "NPC Sheets", "NPC伙伴")):
                in_npc_section = True
            elif in_npc_section and re.match(r"^\s{0,3}##(?!#)", stripped_line) and not any(
                token in stripped_line for token in ("NPC", "关键NPC", "NPC伙伴")
            ):
                in_npc_section = False

            if not in_npc_section and "NPC伙伴" not in stripped_line:
                continue
            if not stripped_line.startswith("#"):
                continue
            if cleaned.startswith("P") and len(cleaned) > 1 and cleaned[1].isdigit():
                continue
            if any(token in cleaned for token in ("NPC 卡", "NPC Sheets")) and "NPC伙伴" not in cleaned:
                continue
            if "NPC伙伴" not in cleaned and not any(token in cleaned for token in ("：", ":", "（", "(")):
                continue

            parsed_name = ""
            identity = ""
            if "NPC伙伴" in cleaned:
                combined = cleaned.split("：", 1)[-1].split(":", 1)[-1].strip()
                suffix_match = re.search(r"([\u4e00-\u9fff]{2,3}|[A-Za-z][A-Za-z·\s'-]{1,24})$", combined)
                parsed_name = suffix_match.group(1).strip() if suffix_match else combined
                if "后裔" in combined and parsed_name.startswith("裔"):
                    parsed_name = parsed_name[1:]
                identity = combined.removesuffix(parsed_name).strip("：: ，,")

            for pattern in NPC_SECTION_PATTERNS:
                if parsed_name:
                    break
                match = pattern.match(cleaned)
                if not match:
                    continue
                groups = [item.strip() for item in match.groups() if item is not None]
                if len(groups) >= 2 and any(token in cleaned for token in ("：", ":")):
                    identity = groups[0]
                    parsed_name = groups[1]
                else:
                    parsed_name = groups[0] if groups else ""
                    identity = groups[1] if len(groups) >= 2 else ""
                if parsed_name:
                    break

            if not parsed_name:
                continue

            detail = ""
            for follow_line in lines[index + 1 : index + 8]:
                stripped = follow_line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    break
                detail_match = re.search(r"(?:身份|细节|潜在动机|动机|美德|利益锚点)[：:]\s*(.+)", stripped)
                if detail_match:
                    detail = detail_match.group(1).strip()
                    break

            candidates[parsed_name] = self._build_story_npc_payload(
                parsed_name,
                identity=identity,
                detail=detail,
                role_hint=identity,
            )

        return candidates

    @staticmethod
    def _collect_actor_ids(state: GameState) -> set[str]:
        actor_ids: set[str] = set()
        for objective in state.scenario.objectives.values():
            actor_ids.update(objective.related_actor_ids)
        for secret in state.scenario.secrets.values():
            actor_ids.update(secret.known_actor_ids)
        return actor_ids

    @staticmethod
    def _actor_id_to_display_name(actor_id: str) -> str:
        suffix = actor_id.strip().split("_")[-1]
        if not suffix:
            return ""
        if suffix.isupper() and len(suffix) <= 8:
            return suffix.title()
        return suffix.replace("-", " ").replace("_", " ").strip()

    def _infer_opening_visible_names(
        self,
        state: GameState,
        candidate_names: Sequence[str],
        *,
        opening_text: str | None = None,
    ) -> list[str]:
        visible_names = list(dict.fromkeys(name for name in state.scene.visible_npcs if name))
        if visible_names:
            return visible_names

        source_text = "\n".join(
            part for part in (self.scenario.opening_scene, opening_text or "", state.scene.description) if part
        )
        for name in candidate_names:
            if name and name in source_text:
                visible_names.append(name)

        for pattern in VISIBLE_NPC_TITLE_PATTERNS:
            for match in pattern.findall(source_text):
                label = match.strip()
                if "的" in label:
                    label = label.split("的")[-1].strip()
                if label and label not in visible_names:
                    visible_names.append(label)

        return list(dict.fromkeys(visible_names))

    def _seed_story_npcs_into_state(self, state: GameState, *, opening_text: str | None = None) -> GameState:
        registry = {name: npc.model_copy(deep=True) for name, npc in state.npcs.items()}
        story_candidates = self._extract_story_npc_candidates()
        visible_names = self._infer_opening_visible_names(
            state,
            list(story_candidates.keys()),
            opening_text=opening_text,
        )

        for name, payload in story_candidates.items():
            payload["location"] = state.scene.location
            payload["task_summary"] = f"???????{state.scenario.current_stage}????"
            payload["last_public_status"] = f"??????{state.scenario.current_arc}????"
            payload["current_goal"] = f"?{state.scenario.current_stage}??????????"
            payload["is_visible"] = name in visible_names
            existing = registry.get(name)
            if existing is None:
                registry[name] = NpcState.model_validate(payload)
                continue

            merged = existing.model_dump(mode="python")
            for key, value in payload.items():
                if key == "tags":
                    merged["tags"] = list(dict.fromkeys([*merged.get("tags", []), *value]))
                    continue
                if value and not merged.get(key):
                    merged[key] = value
            merged["is_visible"] = merged.get("is_visible", False) or name in visible_names
            if not merged.get("location"):
                merged["location"] = state.scene.location
            registry[name] = NpcState.model_validate(merged)

        if not story_candidates:
            for actor_id in self._collect_actor_ids(state):
                if not actor_id.lower().startswith(("npc_", "act_")):
                    continue
                display_name = self._actor_id_to_display_name(actor_id)
                if not display_name or display_name in registry:
                    continue
                registry[display_name] = NpcState(
                    name=display_name,
                    identity=f"Scenario actor {actor_id}",
                    description="Auto-seeded from scenario state actor reference.",
                    location=state.scene.location,
                    is_core_npc=True,
                    outfit="",
                    demeanor="",
                    current_mood="",
                    physical_status="",
                    expression="",
                    current_goal=f"与当前剧情“{state.scenario.current_stage}”相关。",
                    task_summary=f"围绕当前阶段“{state.scenario.current_stage}”行动。",
                    last_public_status="尚未正式出场。",
                    is_visible=display_name in visible_names,
                    tags=["story_seeded", "scenario_actor"],
                )

        updated_scene = state.scene.model_copy(update={"visible_npcs": list(dict.fromkeys(visible_names))})
        updated_core = state.core.model_copy(update={"scene": updated_scene, "npcs": registry})
        return _normalize_initial_state(state.model_copy(update={"core": updated_core}))

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
        language_code: str = "zh-CN",
        difficulty_code: str = "easy",
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
        scenario = repo.load_scenario(
            rule_code=rule_code,
            story_code=story_code,
            language_code=language_code,
            difficulty_code=difficulty_code,
        )
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
            language_code=language_code,
            difficulty_code=difficulty_code,
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
            message=f"[Opening] Building opening prompt context using Beginning prompt: {self.scenario.beginning_prompt_path}",
            payload={
                "rule_code": self.scenario.rule_code,
                "story_code": self.scenario.story_code,
                "beginning_prompt_path": self.scenario.beginning_prompt_path,
                "language_code": self.language_code,
                "difficulty_code": self.difficulty_code,
            },
        )
        messages: list[Message] = [
            {"role": "system", "content": build_output_language_instruction(self.language_code, plain_text=True)},
            {"role": "system", "content": get_opening_language_note(self.language_code)},
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

    def initialize_opening_npc_manager(
        self,
        opening_text: str,
        *,
        progress_callback: Callable[[str], None] | None = None,
        event_callback: Callable[[RuntimeLogEvent], None] | None = None,
    ) -> NPCManagerOutput:
        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="opening",
            stage="seed_npcs",
            message="[Opening] Seeding opening NPC state...",
        )
        with self._state_lock:
            self.state = self._seed_story_npcs_into_state(self.state, opening_text=opening_text)
            working_state = self.state.model_copy(deep=True)

        opening_action = ParsedPlayerAction(
            raw_text="[opening initialization]",
            intent="opening",
            target=None,
            approach=None,
            tags=["opening"],
        )
        npc_context = self.build_npc_context(opening_action, working_state)
        npc_result = self._call_npc_manager_from_context(npc_context)
        npc_result = self._with_structured_core_npc_updates(working_state, npc_result)

        updated_state = apply_delta(working_state, npc_result.state_delta)
        npc_notes = list(updated_state.agent_runtime.npc_manager_notes)
        npc_notes.extend(update.progress for update in npc_result.background_updates if update.progress)
        updated_state = updated_state.model_copy(
            update={
                "agent_runtime": updated_state.agent_runtime.model_copy(
                    update={"npc_manager_notes": npc_notes[-12:]}
                )
            }
        )

        with self._state_lock:
            self.state = updated_state
            self._opening_npc_result = npc_result
            self._opening_npc_initialized = True

        self._emit_runtime_log(
            progress_callback=progress_callback,
            event_callback=event_callback,
            phase="opening",
            stage="npc_manager_ready",
            message="[Opening] NPC Manager initialized opening state.",
            payload={
                "visible_npcs": npc_result.active_visible_npcs,
                "background_npcs": npc_result.active_background_npcs,
            },
        )
        return npc_result

    def get_opening_npc_result(self) -> NPCManagerOutput | None:
        return self._opening_npc_result

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
        action = parse_player_action(player_text, language_code=self.language_code)
        director_state_used = state_before.director.model_copy(deep=True)

        dicer_context = self.build_dicer_context(action, state_before)
        npc_context = self.build_npc_context(action, state_before)

        dicer_future = self._executor.submit(self._call_dicer_from_context, dicer_context)
        npc_future = self._executor.submit(self._call_npc_manager_from_context, npc_context)
        future_to_agent = {
            dicer_future: "dicer",
            npc_future: "npc_manager",
        }
        dicer_result: DicerOutput | None = None
        npc_result: NPCManagerOutput | None = None

        for completed_future in as_completed(future_to_agent):
            agent_name = future_to_agent[completed_future]
            completed_result = completed_future.result()
            if agent_name == "dicer":
                dicer_result = completed_result
            else:
                npc_result = completed_result
            yield TurnStreamEvent(
                event="agent_update",
                agent_name=agent_name,
                payload=completed_result.model_dump(mode="python"),
            )

        if dicer_result is None or npc_result is None:
            raise RuntimeError("Missing parallel agent result during streamed turn")
        npc_result = self._with_structured_core_npc_updates(state_before, npc_result)
        yield TurnStreamEvent(
            event="agent_update",
            agent_name="director_state",
            payload=director_state_used.model_dump(mode="python"),
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
            yield TurnStreamEvent(
                event="agent_update",
                agent_name="director",
                payload=next_director_result.model_dump(mode="python"),
            )
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
        action = parse_player_action(player_text, language_code=self.language_code)
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
        npc_result = self._with_structured_core_npc_updates(state_before, npc_result)

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

    def _call_dicer_from_context(self, context: dict[str, object]) -> DicerOutput:
        messages: list[Message] = [{"role": "system", "content": self.scenario.dicer_prompt}]
        localized_note = get_localized_agent_note("dicer", self.language_code)
        if localized_note:
            messages.append({"role": "system", "content": localized_note})
        messages.extend(
            [
                {"role": "system", "content": build_output_language_instruction(self.language_code, plain_text=False)},
                {
                    "role": "system",
                    "content": (
                        "Use rule_reference, story_reference, rule_state, and scenario_state as stable truth sources. "
                        "Treat history_view as compressed history and state_focus as the most relevant slice for this turn. "
                        "Return state changes only through state_delta, and keep event_log_entries short and factual."
                    ),
                },
                {
                    "role": "user",
                    "content": "Complete this turn's Dicer output from the JSON context below:\n"
                    + json.dumps(context, ensure_ascii=False, indent=2),
                },
            ]
        )

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

        messages: list[Message] = [{"role": "system", "content": self.scenario.npc_manager_prompt}]
        localized_note = get_localized_agent_note("npc_manager", self.language_code)
        if localized_note:
            messages.append({"role": "system", "content": localized_note})
        messages.extend(
            [
                {"role": "system", "content": build_output_language_instruction(self.language_code, plain_text=False)},
                {
                    "role": "system",
                    "content": (
                        "This step runs in parallel with Dicer, so react from player intent, NPC state, scene state, and recent history only. "
                        "Do not wait for Dicer results, do not adjudicate rules, and do not perform macro plotting for Director. "
                        "Keep state_delta focused on NPC and scene changes, and keep event_log_entries short and factual."
                    ),
                },
                {
                    "role": "user",
                    "content": "Complete the NPC Manager output from the JSON context below:\n"
                    + json.dumps(context, ensure_ascii=False, indent=2),
                },
            ]
        )

        return generate_structured_output(
            messages=messages,
            output_schema=NPCManagerOutput,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=self.options.npc_temperature,
        )

    def _call_director_from_context(self, context: dict[str, object]) -> DirectorOutput:
        messages: list[Message] = [{"role": "system", "content": self.scenario.director_prompt}]
        localized_note = get_localized_agent_note("director", self.language_code)
        if localized_note:
            messages.append({"role": "system", "content": localized_note})
        messages.extend(
            [
                {"role": "system", "content": build_output_language_instruction(self.language_code, plain_text=False)},
                {
                    "role": "system",
                    "content": (
                        "Your output affects the next turn rather than rewriting the Narrator text that just completed. "
                        "Use the latest action, Dicer, NPC, Narrator output, and history to shape next-turn pacing and macro progression. "
                        "Do not adjudicate rules for Dicer or generate detailed NPC dialogue for NPC Manager."
                    ),
                },
                {
                    "role": "user",
                    "content": "Complete the next-turn Director output from the JSON context below:\n"
                    + json.dumps(context, ensure_ascii=False, indent=2),
                },
            ]
        )

        return generate_structured_output(
            messages=messages,
            output_schema=DirectorOutput,
            preference_path=self.preference_path,
            registry_path=self.registry_path,
            temperature=self.options.director_temperature,
        )

    def _build_narrator_messages(self, context: dict[str, object]) -> list[Message]:
        messages: list[Message] = [
            {"role": "system", "content": build_output_language_instruction(self.language_code, plain_text=True)},
            {"role": "system", "content": self.scenario.narrator_prompt},
        ]
        localized_note = get_localized_agent_note("narrator", self.language_code)
        if localized_note:
            messages.append({"role": "system", "content": localized_note})
        messages.extend(
            [
                {"role": "system", "content": get_narrator_language_note(self.language_code)},
                {
                    "role": "system",
                    "content": (
                        "Narrator should use the already-completed director_state from the previous turn rather than the newly generated Director result. "
                        "Use dicer_result as the rules-and-outcome baseline, show NPC reactions through npc_result, and translate director_state into tone and pacing. "
                        "Do not invent major new NPC actions, major events, or main-plot breakthroughs."
                    ),
                },
                {
                    "role": "user",
                    "content": "Turn the JSON context below into player-facing narration:\n"
                    + json.dumps(context, ensure_ascii=False, indent=2),
                },
            ]
        )
        return messages

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

    @staticmethod
    def _with_structured_core_npc_updates(
        state: GameState,
        npc_result: NPCManagerOutput,
    ) -> NPCManagerOutput:
        if not npc_result.core_npc_updates:
            return npc_result

        derived_delta = list(npc_result.state_delta)
        tracked_fields = (
            "appearance",
            "outfit",
            "demeanor",
            "current_mood",
            "physical_status",
            "expression",
            "last_public_status",
        )
        existing_set_paths = {
            operation.path
            for operation in derived_delta
            if operation.op == "set"
        }

        for update in npc_result.core_npc_updates:
            npc_name = update.npc_name.strip()
            if not npc_name:
                continue
            current_npc = state.npcs.get(npc_name)
            if current_npc is None:
                continue

            core_flag_path = f"core.npcs.{npc_name}.is_core_npc"
            if core_flag_path not in existing_set_paths and not current_npc.is_core_npc:
                derived_delta.append(DeltaOperation(op="set", path=core_flag_path, value=True))
                existing_set_paths.add(core_flag_path)

            for field_name in tracked_fields:
                new_value = getattr(update, field_name, "").strip()
                if not new_value:
                    continue
                if getattr(current_npc, field_name, "").strip() == new_value:
                    continue
                path = f"core.npcs.{npc_name}.{field_name}"
                if path in existing_set_paths:
                    continue
                derived_delta.append(DeltaOperation(op="set", path=path, value=new_value))
                existing_set_paths.add(path)

        if derived_delta == npc_result.state_delta:
            return npc_result
        return npc_result.model_copy(update={"state_delta": derived_delta})
