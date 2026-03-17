from __future__ import annotations

from typing import Any

from .models import DeltaOperation, GameMeta, GameState


def _resolve_parent(container: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    parts = path.split(".")
    current = container
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    return current, parts[-1]


def apply_delta(state: GameState, delta: list[DeltaOperation]) -> GameState:
    working = state.model_dump(mode="python")

    for operation in delta:
        parent, leaf = _resolve_parent(working, operation.path)

        if operation.op == "set":
            parent[leaf] = operation.value
            continue

        if operation.op == "append":
            target = parent.get(leaf)
            if not isinstance(target, list):
                target = []
                parent[leaf] = target
            target.append(operation.value)
            continue

        if operation.op == "inc":
            current_value = parent.get(leaf, 0)
            if not isinstance(current_value, (int, float)):
                raise TypeError(f"Cannot increment non-numeric field: {operation.path}")
            parent[leaf] = current_value + operation.value
            continue

        raise ValueError(f"Unsupported delta op: {operation.op}")

    return GameState.model_validate(working)


def advance_clock(meta: GameMeta, minutes: int) -> GameMeta:
    total_minutes = meta.game_hour * 60 + meta.game_minute + minutes
    extra_days, remainder = divmod(total_minutes, 24 * 60)
    hour, minute = divmod(remainder, 60)
    return meta.model_copy(
        update={
            "game_day": meta.game_day + extra_days,
            "game_hour": hour,
            "game_minute": minute,
        }
    )


def format_game_time(meta: GameMeta) -> str:
    return f"D{meta.game_day} {meta.game_hour:02d}:{meta.game_minute:02d}"


def append_recent_events(
    state: GameState,
    entries: list[str],
    *,
    max_recent_events: int = 5,
    max_summary_chars: int = 1600,
) -> GameState:
    if not entries:
        return state

    payload = state.model_dump(mode="python")
    payload["recent_events"].extend(entry for entry in entries if entry)

    if len(payload["recent_events"]) > max_recent_events:
        overflow = payload["recent_events"][:-max_recent_events]
        payload["recent_events"] = payload["recent_events"][-max_recent_events:]
        merged = payload.get("chapter_summary", "").strip()
        overflow_text = "；".join(overflow)
        if merged:
            merged = f"{merged}\n归档事件：{overflow_text}"
        else:
            merged = f"归档事件：{overflow_text}"
        if len(merged) > max_summary_chars:
            merged = merged[-max_summary_chars:]
        payload["chapter_summary"] = merged

    return GameState.model_validate(payload)

