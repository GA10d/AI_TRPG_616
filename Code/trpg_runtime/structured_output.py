from __future__ import annotations

import json
from typing import Sequence, TypeVar

from pydantic import BaseModel

from text_model.function_TextGeneration import (
    Message,
    get_normal_reply,
    get_selected_text_model_config,
    get_structured_reply,
)


StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


JSON_ONLY_INSTRUCTION = (
    "Return only valid JSON that matches the target schema. "
    "Do not include markdown fences, commentary, or extra text."
)


def _supports_native_structured(
    *,
    preference_path: str | None = None,
    registry_path: str | None = None,
) -> bool:
    config = get_selected_text_model_config(
        preference_path=preference_path,
        registry_path=registry_path,
    )
    return config.dependence == "OpenAI" and config.base_url is None


def _strip_markdown_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if not lines:
        return text

    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object(raw: str) -> str:
    text = _strip_markdown_fence(raw)
    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return text[start : end + 1]


def generate_structured_output(
    *,
    messages: Sequence[Message],
    output_schema: type[StructuredModelT],
    preference_path: str | None = None,
    registry_path: str | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> StructuredModelT:
    if _supports_native_structured(
        preference_path=preference_path,
        registry_path=registry_path,
    ):
        try:
            return get_structured_reply(
                input=messages,
                output_schema=output_schema,
                feature="json_output",
                preference_path=preference_path,
                registry_path=registry_path,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            if not _should_fallback_to_json_prompt(exc):
                raise

    fallback_messages = list(messages) + [
        {"role": "system", "content": JSON_ONLY_INSTRUCTION}
    ]
    raw = get_normal_reply(
        input=fallback_messages,
        feature="json_output",
        preference_path=preference_path,
        registry_path=registry_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    json_payload = _extract_json_object(raw)
    parsed = json.loads(json_payload)
    return output_schema.model_validate(parsed)


def _should_fallback_to_json_prompt(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "invalid schema" in text
        or "invalid_json_schema" in text
        or "response_format" in text
        or "text.format.schema" in text
    )
