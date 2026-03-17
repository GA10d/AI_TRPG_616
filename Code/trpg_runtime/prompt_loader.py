from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import (
    ObjectiveState,
    RuleSeed,
    RuleState,
    ScenarioSeed,
    ScenarioState,
    SecretState,
    TriggerState,
    build_rule_state,
)
from .structured_output import generate_structured_output


REPO_ROOT = Path(__file__).resolve().parents[2]
PRIMARY_STORY_ROOT = REPO_ROOT / "Story"
SECONDARY_STORY_ROOT = REPO_ROOT / "story"
LEGACY_PROMPT_ROOT = REPO_ROOT / "Prompt"
AGENT_PROMPT_ROOT = REPO_ROOT / "Code" / "data" / "data_TextPrompt"

JSON_PROMPT_TEMPERATURE = 0.1


def _resolve_default_story_root() -> Path:
    if PRIMARY_STORY_ROOT.exists():
        return PRIMARY_STORY_ROOT
    if SECONDARY_STORY_ROOT.exists():
        return SECONDARY_STORY_ROOT
    return PRIMARY_STORY_ROOT


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"State config must be a JSON object: {path}")
    return data


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _compact_text(text: str, max_chars: int) -> str:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        return stripped
    return "Untitled Scenario"


def _extract_opening_scene(text: str, max_chars: int = 480) -> str:
    lines = []
    seen_body = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") and not seen_body:
            continue
        seen_body = True
        lines.append(line)
        if len(" ".join(lines)) >= max_chars:
            break
    return _compact_text("\n".join(lines), max_chars=max_chars)


def _merge_nested_dicts(base: dict[str, object], patch: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in patch.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _merge_nested_dicts(base_value, value)
        else:
            merged[key] = value
    return merged


def _wrap_state_layer(file_name: str, payload: dict[str, object]) -> dict[str, object]:
    lower_name = file_name.casefold()
    layer_names = ("core", "rule", "scenario", "agent_runtime")
    for layer_name in layer_names:
        if lower_name.endswith(f"{layer_name}_state.json"):
            return {layer_name: payload}
    return payload


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _slugify_identifier(text: str, prefix: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    if normalized:
        return normalized
    return prefix


@dataclass(frozen=True)
class ScenarioBundle:
    rule_code: str
    story_code: str
    rule_text: str
    story_text: str
    beginning_prompt: str
    dicer_prompt: str
    npc_manager_prompt: str
    director_prompt: str
    narrator_prompt: str
    title: str
    opening_scene: str
    story_summary: str

    def rule_excerpt(self, max_chars: int) -> str:
        return _compact_text(self.rule_text, max_chars=max_chars)

    def story_excerpt(self, max_chars: int) -> str:
        return _compact_text(self.story_text, max_chars=max_chars)


class PromptRepository:
    def __init__(
        self,
        prompt_root: Path | None = None,
        legacy_prompt_root: Path = LEGACY_PROMPT_ROOT,
        agent_prompt_root: Path = AGENT_PROMPT_ROOT,
    ) -> None:
        self.prompt_root = prompt_root or _resolve_default_story_root()
        self.legacy_prompt_root = legacy_prompt_root
        self.agent_prompt_root = agent_prompt_root

    def list_rule_codes(self) -> list[str]:
        result: set[str] = set()

        if self.prompt_root.exists():
            for candidate in self.prompt_root.iterdir():
                if not candidate.is_dir():
                    continue
                rule_dir = candidate / "Rule"
                expected = rule_dir / f"{candidate.name.upper()}_PROMPT.txt"
                if expected.exists():
                    result.add(candidate.name.upper())

        legacy_rule_dir = self.legacy_prompt_root / "Rule"
        if legacy_rule_dir.exists():
            for path in legacy_rule_dir.glob("*_PROMPT.txt"):
                result.add(path.stem.replace("_PROMPT", "").upper())

        return sorted(result)

    def list_story_codes(self, rule_code: str) -> list[str]:
        story_dir = self._story_dir(rule_code.upper())
        if not story_dir.exists():
            return []
        result: set[str] = set()
        result.update(path.stem for path in story_dir.glob("*.txt"))
        for child in story_dir.iterdir():
            if not child.is_dir():
                continue
            if any(candidate.is_file() and candidate.suffix.lower() == ".txt" for candidate in child.iterdir()):
                result.add(child.name)
        return sorted(result)

    def load_scenario(self, rule_code: str, story_code: str) -> ScenarioBundle:
        normalized_rule = rule_code.upper()

        rule_path = self._rule_dir(normalized_rule) / f"{normalized_rule}_PROMPT.txt"
        story_path = self._resolve_story_path(normalized_rule, story_code)
        beginning_path = self._resolve_beginning_prompt_path()

        if not rule_path.exists():
            raise FileNotFoundError(rule_path)
        if not story_path.exists():
            raise FileNotFoundError(story_path)

        rule_text = _read_text(rule_path)
        story_text = _read_text(story_path)
        beginning_prompt = _read_text(beginning_path)

        dicer_prompt = _read_text(self.agent_prompt_root / "data_Dicer.txt")
        npc_manager_prompt = _read_text(self.agent_prompt_root / "data_NpcManager.txt")
        director_prompt = _read_text(self.agent_prompt_root / "data_Director.txt")
        narrator_prompt = _read_text(self.agent_prompt_root / "data_Narrator.txt")

        return ScenarioBundle(
            rule_code=normalized_rule,
            story_code=story_path.stem,
            rule_text=rule_text,
            story_text=story_text,
            beginning_prompt=beginning_prompt,
            dicer_prompt=dicer_prompt,
            npc_manager_prompt=npc_manager_prompt,
            director_prompt=director_prompt,
            narrator_prompt=narrator_prompt,
            title=_extract_title(story_text),
            opening_scene=_extract_opening_scene(story_text),
            story_summary=_compact_text(story_text, max_chars=900),
        )

    def load_state_overrides(
        self,
        rule_code: str,
        story_code: str,
        *,
        scenario: ScenarioBundle | None = None,
        preference_path: str | None = None,
        registry_path: str | None = None,
        extra_override_path: str | Path | None = None,
        auto_parse_missing: bool = True,
        persist_generated: bool = True,
    ) -> dict[str, object]:
        scenario_bundle = scenario or self.load_scenario(rule_code, story_code)
        normalized_rule = rule_code.upper()
        story_path = self._resolve_story_path(normalized_rule, story_code)
        story_state_dir = story_path.parent
        story_stem = story_path.stem
        rule_dir = self._rule_dir(normalized_rule)

        merged: dict[str, object] = {}
        merged = _merge_nested_dicts(
            merged,
            self._load_generic_overrides(
                rule_dir=rule_dir,
                story_state_dir=story_state_dir,
                story_stem=story_stem,
            ),
        )

        rule_state = self._load_rule_state(
            scenario=scenario_bundle,
            rule_dir=rule_dir,
            preference_path=preference_path,
            registry_path=registry_path,
            auto_parse_missing=auto_parse_missing,
            persist_generated=persist_generated,
        )
        scenario_state = self._load_scenario_state(
            scenario=scenario_bundle,
            story_state_dir=story_state_dir,
            story_stem=story_stem,
            preference_path=preference_path,
            registry_path=registry_path,
            auto_parse_missing=auto_parse_missing,
            persist_generated=persist_generated,
        )

        merged = _merge_nested_dicts(merged, {"rule": rule_state.model_dump(mode="python")})
        merged = _merge_nested_dicts(merged, {"scenario": scenario_state.model_dump(mode="python")})

        if extra_override_path is not None:
            extra_path = Path(extra_override_path)
            if extra_path.exists():
                merged = _merge_nested_dicts(
                    merged,
                    _wrap_state_layer(extra_path.name, _read_json(extra_path)),
                )
        return merged

    def parse_story_to_seed(
        self,
        *,
        scenario: ScenarioBundle,
        preference_path: str | None = None,
        registry_path: str | None = None,
    ) -> ScenarioSeed:
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract a conservative TRPG scenario seed from story text. "
                    "Only include objectives, triggers, secrets, facts, foreshadow, and endings that are explicit "
                    "or strongly implied by the text. Prefer omission over invention. "
                    "Keep strings short and operational."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "rule_code": scenario.rule_code,
                        "story_code": scenario.story_code,
                        "story_title": scenario.title,
                        "opening_scene": scenario.opening_scene,
                        "story_text": scenario.story_text,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        return generate_structured_output(
            messages=messages,
            output_schema=ScenarioSeed,
            preference_path=preference_path,
            registry_path=registry_path,
            temperature=JSON_PROMPT_TEMPERATURE,
        )

    def parse_rule_to_seed(
        self,
        *,
        scenario: ScenarioBundle,
        preference_path: str | None = None,
        registry_path: str | None = None,
    ) -> RuleSeed:
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract a conservative TRPG rule seed from rule text. "
                    "Only include starting mechanics, meters, resources, flags, and extension fields that are explicit "
                    "or clearly implied. Prefer empty values over invented values. "
                    "Always set the 'family' field to the provided rule_code."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "rule_code": scenario.rule_code,
                        "rule_text": scenario.rule_text,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        return generate_structured_output(
            messages=messages,
            output_schema=RuleSeed,
            preference_path=preference_path,
            registry_path=registry_path,
            temperature=JSON_PROMPT_TEMPERATURE,
        )

    def _load_generic_overrides(
        self,
        *,
        rule_dir: Path,
        story_state_dir: Path,
        story_stem: str,
    ) -> dict[str, object]:
        candidates = [
            rule_dir / "state_overrides.json",
            rule_dir / "core_state.json",
            rule_dir / "agent_runtime_state.json",
            story_state_dir / "state_overrides.json",
            story_state_dir / "core_state.json",
            story_state_dir / "agent_runtime_state.json",
            story_state_dir / f"{story_stem}.state_overrides.json",
            story_state_dir / f"{story_stem}.core_state.json",
            story_state_dir / f"{story_stem}.agent_runtime_state.json",
        ]

        merged: dict[str, object] = {}
        for candidate in candidates:
            if not candidate.exists():
                continue
            payload = _wrap_state_layer(candidate.name, _read_json(candidate))
            if "rule" in payload:
                payload.pop("rule")
            if "scenario" in payload:
                payload.pop("scenario")
            merged = _merge_nested_dicts(merged, payload)
        return merged

    def _load_rule_state(
        self,
        *,
        scenario: ScenarioBundle,
        rule_dir: Path,
        preference_path: str | None,
        registry_path: str | None,
        auto_parse_missing: bool,
        persist_generated: bool,
    ) -> RuleState:
        candidates = [
            rule_dir / "rule_state.json",
            rule_dir / "state_overrides.json",
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            raw_payload = _read_json(candidate)
            payload = raw_payload.get("rule") if candidate.name == "state_overrides.json" else raw_payload
            if not isinstance(payload, dict):
                continue
            return self._audit_rule_state(payload, scenario.rule_code, source_path=candidate)

        if auto_parse_missing:
            seed = self.parse_rule_to_seed(
                scenario=scenario,
                preference_path=preference_path,
                registry_path=registry_path,
            )
            state = self._rule_state_from_seed(seed, scenario.rule_code)
            if persist_generated:
                _write_json(rule_dir / "rule_state.json", state.model_dump(mode="python"))
            return state

        return build_rule_state(scenario.rule_code)

    def _load_scenario_state(
        self,
        *,
        scenario: ScenarioBundle,
        story_state_dir: Path,
        story_stem: str,
        preference_path: str | None,
        registry_path: str | None,
        auto_parse_missing: bool,
        persist_generated: bool,
    ) -> ScenarioState:
        candidates = [
            story_state_dir / "scenario_state.json",
            story_state_dir / "state_overrides.json",
            story_state_dir / f"{story_stem}.scenario_state.json",
            story_state_dir / f"{story_stem}.state_overrides.json",
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            raw_payload = _read_json(candidate)
            payload = raw_payload.get("scenario") if candidate.name.endswith("state_overrides.json") else raw_payload
            if not isinstance(payload, dict):
                continue
            return self._audit_scenario_state(payload, scenario, source_path=candidate)

        if auto_parse_missing:
            seed = self.parse_story_to_seed(
                scenario=scenario,
                preference_path=preference_path,
                registry_path=registry_path,
            )
            state = self._scenario_state_from_seed(seed, scenario)
            if persist_generated:
                _write_json(story_state_dir / "scenario_state.json", state.model_dump(mode="python"))
            return state

        return ScenarioState(
            title=scenario.title,
            brief=scenario.story_summary,
            opening_scene=scenario.opening_scene,
        )

    def _audit_rule_state(
        self,
        payload: dict[str, object],
        rule_code: str,
        *,
        source_path: Path,
    ) -> RuleState:
        base = build_rule_state(rule_code).model_dump(mode="python")
        merged = _merge_nested_dicts(base, payload)
        merged["family"] = rule_code.upper()
        try:
            return RuleState.model_validate(merged)
        except Exception as exc:
            raise ValueError(f"Invalid rule state file: {source_path}") from exc

    def _rule_state_from_seed(self, seed: RuleSeed, rule_code: str) -> RuleState:
        base = build_rule_state(rule_code).model_dump(mode="python")
        merged = _merge_nested_dicts(base, seed.model_dump(mode="python", exclude_none=True))
        merged["family"] = rule_code.upper()
        return RuleState.model_validate(merged)

    def _audit_scenario_state(
        self,
        payload: dict[str, object],
        scenario: ScenarioBundle,
        *,
        source_path: Path,
    ) -> ScenarioState:
        base = ScenarioState(
            title=scenario.title,
            brief=scenario.story_summary,
            opening_scene=scenario.opening_scene,
        ).model_dump(mode="python")
        normalized = dict(payload)
        normalized["objectives"] = self._normalize_state_mapping(
            payload.get("objectives"),
            id_field="objective_id",
            title_field="title",
            prefix="objective",
        )
        normalized["triggers"] = self._normalize_state_mapping(
            payload.get("triggers"),
            id_field="trigger_id",
            title_field="condition_summary",
            prefix="trigger",
        )
        normalized["secrets"] = self._normalize_state_mapping(
            payload.get("secrets"),
            id_field="secret_id",
            title_field="title",
            prefix="secret",
        )
        normalized["world_facts"] = _dedupe_preserve_order(list(normalized.get("world_facts", [])))
        normalized["unresolved_questions"] = _dedupe_preserve_order(list(normalized.get("unresolved_questions", [])))
        normalized["active_branch_flags"] = _dedupe_preserve_order(list(normalized.get("active_branch_flags", [])))
        normalized["foreshadow_queue"] = _dedupe_preserve_order(list(normalized.get("foreshadow_queue", [])))
        normalized["ending_candidates"] = _dedupe_preserve_order(list(normalized.get("ending_candidates", [])))

        merged = _merge_nested_dicts(base, normalized)
        try:
            return ScenarioState.model_validate(merged)
        except Exception as exc:
            raise ValueError(f"Invalid scenario state file: {source_path}") from exc

    def _scenario_state_from_seed(self, seed: ScenarioSeed, scenario: ScenarioBundle) -> ScenarioState:
        objectives: dict[str, dict[str, object]] = {}
        for index, objective in enumerate(seed.objectives, start=1):
            objective_id = objective.objective_id or _slugify_identifier(objective.title, f"objective_{index}")
            objective_payload = objective.model_dump(mode="python")
            objective_payload["objective_id"] = objective_id
            objectives[objective_id] = objective_payload

        triggers: dict[str, dict[str, object]] = {}
        for index, trigger in enumerate(seed.triggers, start=1):
            trigger_id = trigger.trigger_id or _slugify_identifier(trigger.condition_summary, f"trigger_{index}")
            trigger_payload = trigger.model_dump(mode="python")
            trigger_payload["trigger_id"] = trigger_id
            triggers[trigger_id] = trigger_payload

        secrets: dict[str, dict[str, object]] = {}
        for index, secret in enumerate(seed.secrets, start=1):
            secret_id = secret.secret_id or _slugify_identifier(secret.title, f"secret_{index}")
            secret_payload = secret.model_dump(mode="python")
            secret_payload["secret_id"] = secret_id
            secrets[secret_id] = secret_payload

        return ScenarioState.model_validate(
            {
                "title": seed.title or scenario.title,
                "brief": seed.brief or scenario.story_summary,
                "opening_scene": seed.opening_scene or scenario.opening_scene,
                "current_arc": seed.current_arc or "opening",
                "current_stage": seed.current_stage or "opening",
                "objectives": objectives,
                "triggers": triggers,
                "secrets": secrets,
                "world_facts": _dedupe_preserve_order(seed.world_facts),
                "unresolved_questions": _dedupe_preserve_order(seed.unresolved_questions),
                "active_branch_flags": _dedupe_preserve_order(seed.active_branch_flags),
                "foreshadow_queue": _dedupe_preserve_order(seed.foreshadow_queue),
                "ending_candidates": _dedupe_preserve_order(seed.ending_candidates),
                "fail_state": seed.fail_state,
            }
        )

    def _normalize_state_mapping(
        self,
        raw: object,
        *,
        id_field: str,
        title_field: str,
        prefix: str,
    ) -> dict[str, dict[str, object]]:
        if raw is None:
            return {}

        result: dict[str, dict[str, object]] = {}
        used_ids: set[str] = set()

        if isinstance(raw, dict):
            items = []
            for key, value in raw.items():
                if not isinstance(value, dict):
                    raise TypeError(f"Expected object values in mapping for {prefix}")
                payload = dict(value)
                payload.setdefault(id_field, key)
                items.append(payload)
        elif isinstance(raw, list):
            items = []
            for value in raw:
                if not isinstance(value, dict):
                    raise TypeError(f"Expected object items in list for {prefix}")
                items.append(dict(value))
        else:
            raise TypeError(f"Expected dict or list for {prefix}")

        for index, payload in enumerate(items, start=1):
            raw_identifier = str(payload.get(id_field, "")).strip()
            title_value = str(payload.get(title_field, "")).strip()
            identifier = raw_identifier or _slugify_identifier(title_value, f"{prefix}_{index}")
            base_identifier = identifier
            suffix = 2
            while identifier in used_ids:
                identifier = f"{base_identifier}_{suffix}"
                suffix += 1
            used_ids.add(identifier)
            payload[id_field] = identifier
            result[identifier] = payload
        return result

    def _resolve_beginning_prompt_path(self) -> Path:
        new_path = self.agent_prompt_root / "data_Beginning.txt"
        if new_path.exists():
            return new_path

        legacy_path = self.legacy_prompt_root / "Function" / "BEGINNING_PROMPT.txt"
        if legacy_path.exists():
            return legacy_path

        raise FileNotFoundError(new_path)

    def _rule_dir(self, rule_code: str) -> Path:
        new_dir = self.prompt_root / rule_code.upper() / "Rule"
        if new_dir.exists():
            return new_dir
        fallback_dir = SECONDARY_STORY_ROOT / rule_code.upper() / "Rule"
        if fallback_dir.exists():
            return fallback_dir
        return self.legacy_prompt_root / "Rule"

    def _story_dir(self, rule_code: str) -> Path:
        new_dir = self.prompt_root / rule_code.upper() / "Story"
        if new_dir.exists():
            return new_dir
        fallback_dir = SECONDARY_STORY_ROOT / rule_code.upper() / "Story"
        if fallback_dir.exists():
            return fallback_dir
        return self.legacy_prompt_root / "Story" / rule_code.upper()

    def _resolve_story_path(self, rule_code: str, story_code: str) -> Path:
        story_dir = self._story_dir(rule_code)
        if not story_dir.exists():
            raise FileNotFoundError(story_dir)

        exact = story_dir / f"{story_code}.txt"
        if exact.exists():
            return exact

        nested_exact = story_dir / story_code / f"{story_code}.txt"
        if nested_exact.exists():
            return nested_exact

        normalized_target = story_code.casefold()
        for candidate in story_dir.iterdir():
            if not candidate.is_dir():
                continue
            if candidate.name.casefold() != normalized_target:
                continue
            direct_txt = candidate / f"{candidate.name}.txt"
            if direct_txt.exists():
                return direct_txt
            for txt_file in candidate.glob("*.txt"):
                return txt_file
        for candidate in story_dir.glob("*.txt"):
            if candidate.stem.casefold() == normalized_target:
                return candidate
        for candidate in story_dir.glob("*/*.txt"):
            if candidate.stem.casefold() == normalized_target:
                return candidate

        raise FileNotFoundError(story_dir / f"{story_code}.txt")
