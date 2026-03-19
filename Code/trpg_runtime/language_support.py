from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from user_info.option_Language import OptionLanguage


LANGUAGE_ALIASES = {
    "zh": "zh-cn",
    "zh-hans": "zh-cn",
    "zh-cn": "zh-cn",
    "zh-sg": "zh-cn",
    "zh-hant": "zh-tw",
    "zh-hk": "zh-tw",
    "zh-mo": "zh-tw",
    "zh-tw": "zh-tw",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "ja": "ja",
    "ja-jp": "ja",
    "ko": "ko",
    "ko-kr": "ko",
}

PROMPT_LOCALIZED_LANGUAGE_CODES = {"zh-cn", "zh-tw", "en", "ja"}
LANGUAGE_DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "data_Language"
DEFAULT_LANGUAGE_CODE = "en"
DEFAULT_DIFFICULTY_CODE = "easy"
DIFFICULTY_OPTIONS = (
    {"code": "easy", "label": "Easy"},
    {"code": "hard", "label": "Hard"},
)


def normalize_language_code(code: str | None) -> str:
    if not code:
        return "zh-CN"
    normalized = code.strip().casefold()
    resolved = LANGUAGE_ALIASES.get(normalized, normalized)
    if resolved == "zh-cn":
        return "zh-CN"
    if resolved == "zh-tw":
        return "zh-TW"
    return resolved


def normalize_difficulty_code(code: str | None) -> str:
    if not code:
        return DEFAULT_DIFFICULTY_CODE
    normalized = code.strip().casefold()
    if normalized in {"easy", "hard"}:
        return normalized
    return DEFAULT_DIFFICULTY_CODE


def _pack_suffix(code: str | None) -> str:
    normalized = normalize_language_code(code)
    return normalized


def _pack_dir(code: str | None) -> Path:
    return LANGUAGE_DATA_ROOT / f"data_{_pack_suffix(code)}"


def get_prompt_bundle_language(code: str | None) -> str | None:
    normalized = normalize_language_code(code).casefold()
    if normalized in PROMPT_LOCALIZED_LANGUAGE_CODES and (_pack_dir(code) / "prompts").exists():
        return normalized
    return None


def is_prompt_localized_language(code: str | None) -> bool:
    return get_prompt_bundle_language(code) is not None


@lru_cache(maxsize=16)
def load_language_pack(code: str | None) -> dict[str, object]:
    pack_path = _pack_dir(code) / "pack.json"
    if not pack_path.exists():
        pack_path = _pack_dir(DEFAULT_LANGUAGE_CODE) / "pack.json"
    payload = json.loads(pack_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Language pack must be a JSON object")
    return payload


@lru_cache(maxsize=16)
def load_exact_language_pack(code: str | None) -> dict[str, object] | None:
    pack_path = _pack_dir(code) / "pack.json"
    if not pack_path.exists():
        return None
    payload = json.loads(pack_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Language pack must be a JSON object")
    return payload


def get_language_pack_payload(code: str | None) -> dict[str, object]:
    requested = normalize_language_code(code)
    pack = dict(load_language_pack(code))
    pack["requested_code"] = requested
    pack["effective_code"] = _pack_suffix(code) if (_pack_dir(code) / "pack.json").exists() else DEFAULT_LANGUAGE_CODE
    pack["prompt_localized"] = is_prompt_localized_language(code)
    return pack


def build_output_language_instruction(code: str | None, *, plain_text: bool) -> str:
    language = OptionLanguage.from_code(normalize_language_code(code))
    if plain_text:
        return f"Reply strictly in {language.label} ({language.code}). Write all player-visible prose only in that language."
    return (
        f"Keep the required JSON schema and keys unchanged. "
        f"Whenever you generate free-text field values, write them in {language.label} ({language.code})."
    )


def _get_note(code: str | None, key: str) -> str | None:
    pack = load_exact_language_pack(code)
    if pack is None:
        return None
    notes = pack.get("notes", {})
    if not isinstance(notes, dict):
        return None
    value = notes.get(key)
    return value if isinstance(value, str) and value.strip() else None


def get_localized_agent_note(agent_name: str, code: str | None) -> str | None:
    return _get_note(code, f"agent_{agent_name}")


def get_opening_language_note(code: str | None) -> str:
    return _get_note(code, "opening") or build_output_language_instruction(code, plain_text=True)


def get_narrator_language_note(code: str | None) -> str:
    return _get_note(code, "narrator") or build_output_language_instruction(code, plain_text=True)


def get_prompt_path(code: str | None, file_name: str, *, difficulty_code: str | None = None) -> Path | None:
    normalized_difficulty = normalize_difficulty_code(difficulty_code)
    candidates = [
        _pack_dir(code) / "prompts" / normalized_difficulty / file_name,
        _pack_dir(code) / "prompts" / file_name,
        _pack_dir(DEFAULT_LANGUAGE_CODE) / "prompts" / normalized_difficulty / file_name,
        _pack_dir(DEFAULT_LANGUAGE_CODE) / "prompts" / file_name,
    ]
    for prompt_path in candidates:
        if prompt_path.exists():
            return prompt_path
    return None


def get_action_parser_path(code: str | None) -> Path | None:
    candidates = [
        _pack_dir(code) / "action_parser.json",
        _pack_dir(DEFAULT_LANGUAGE_CODE) / "action_parser.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def build_language_options_payload() -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    for option in OptionLanguage.list_options():
        payload = option.to_payload()
        payload["prompt_localized"] = is_prompt_localized_language(option.code)
        options.append(payload)
    return options


def build_difficulty_options_payload() -> list[dict[str, object]]:
    return [dict(option) for option in DIFFICULTY_OPTIONS]
