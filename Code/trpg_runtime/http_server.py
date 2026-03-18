from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_ROOT = REPO_ROOT / "Code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from trpg_runtime import GameState, MinimalTRPGEngine, PromptRepository, RuntimeOptions
from trpg_runtime.language_support import build_language_options_payload, get_language_pack_payload, normalize_language_code

SAVE_ROOT = REPO_ROOT / "Save"
SESSION_SAVE_DIR = SAVE_ROOT / "session_saves"
HISTORY_EXPORT_DIR = SAVE_ROOT / "history_exports"


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _stream_response_start(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Connection", "close")
    handler.end_headers()


def _stream_response_write(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
    body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    handler.wfile.write(body)
    handler.wfile.flush()


def _read_json_request(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _coerce_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    parsed = int(value)
    return max(minimum, min(maximum, parsed))


def _coerce_str(value: object, *, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _serialize_state_summary(state: Any) -> dict[str, Any]:
    player = state.player
    scene = state.scene
    meta = state.meta

    return {
        "turn_id": meta.turn_id,
        "game_time": {
            "day": meta.game_day,
            "hour": meta.game_hour,
            "minute": meta.game_minute,
        },
        "player": {
            "name": player.name,
            "status": list(player.status),
            "inventory": list(player.inventory),
            "known_clues": list(player.known_clues),
            "short_term_goals": list(player.short_term_goals),
            "relationship_notes": list(player.relationship_notes),
        },
        "scene": {
            "location": scene.location,
            "description": scene.description,
            "visible_npcs": list(scene.visible_npcs),
            "interactive_objects": list(scene.interactive_objects),
            "hazards": list(scene.hazards),
        },
        "recent_events": list(state.recent_events),
        "scenario": {
            "title": state.scenario_title,
            "brief": state.scenario_brief,
            "opening_scene": state.scenario.opening_scene,
        },
        "rule_family": state.rule.family,
    }


@dataclass
class SessionRecord:
    session_id: str
    engine: MinimalTRPGEngine
    rule_code: str
    story_code: str
    player_name: str
    language_code: str
    max_turns: int
    opening: str
    created_at: float
    lock: threading.RLock
    options: RuntimeOptions
    transcript: list[dict[str, Any]]

    @property
    def turns_used(self) -> int:
        return int(self.engine.state.meta.turn_id)

    @property
    def turns_remaining(self) -> int:
        return max(self.max_turns - self.turns_used, 0)

    @property
    def is_finished(self) -> bool:
        return self.turns_used >= self.max_turns


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, record: SessionRecord) -> None:
        with self._lock:
            self._sessions[record.session_id] = record

    def get(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions.pop(session_id, None)


def _build_catalog(repo: PromptRepository) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for rule_code in repo.list_rule_codes():
        stories: list[dict[str, str]] = []
        for story_code in repo.list_story_codes(rule_code):
            try:
                bundle = repo.load_scenario(rule_code=rule_code, story_code=story_code)
                stories.append(
                    {
                        "story_code": bundle.story_code,
                        "title": bundle.title,
                        "opening_scene": bundle.opening_scene,
                    }
                )
            except Exception:
                stories.append(
                    {
                        "story_code": story_code,
                        "title": story_code,
                        "opening_scene": "",
                    }
                )
        catalog.append({"rule_code": rule_code, "stories": stories})
    return catalog


def _slugify(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in value)
    return "_".join(part for part in cleaned.split("_") if part) or "session"


def _timestamp_tag() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _build_save_name(record: SessionRecord, *, extension: str) -> str:
    player_tag = _slugify(record.player_name)[:24]
    turn_tag = f"t{record.turns_used:03d}"
    return (
        f"trpg_{_slugify(record.rule_code)}_{_slugify(record.story_code)}_"
        f"{player_tag}_{turn_tag}_{record.session_id}_{_timestamp_tag()}.{extension}"
    )


def _serialize_transcript(record: SessionRecord) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = []
    for item in record.transcript:
        role = str(item.get("role", "")).strip()
        if role not in {"system", "player", "ai"}:
            continue
        transcript.append(
            {
                "role": role,
                "content": str(item.get("content", "")),
                "created_at": item.get("created_at"),
            }
        )
    return transcript


def _serialize_transcript_text(record: SessionRecord) -> str:
    lines: list[str] = [
        f"Session ID: {record.session_id}",
        f"Rule: {record.rule_code}",
        f"Story: {record.story_code}",
        f"Player: {record.player_name}",
        "",
    ]
    for item in record.transcript:
        role = str(item.get("role", "")).strip()
        if role not in {"player", "ai"}:
            continue
        speaker = "玩家" if role == "player" else "主持人"
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"[{speaker}]")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _serialize_save_payload(record: SessionRecord) -> dict[str, Any]:
    return {
        "version": 2,
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "session": {
            "session_id": record.session_id,
            "rule_code": record.rule_code,
            "story_code": record.story_code,
            "player_name": record.player_name,
            "max_turns": record.max_turns,
            "opening": record.opening,
            "created_at": record.created_at,
            "options": asdict(record.options),
            "state": record.engine.state.model_dump(mode="python"),
            "transcript": _serialize_transcript(record),
        },
    }


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _list_save_entries() -> list[dict[str, Any]]:
    if not SESSION_SAVE_DIR.exists():
        return []

    entries: list[dict[str, Any]] = []
    for path in sorted(SESSION_SAVE_DIR.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            session = payload.get("session", {})
            state = session.get("state", {})
            meta = state.get("core", {}).get("meta", {})
            entries.append(
                {
                    "file_name": path.name,
                    "saved_at": payload.get("saved_at"),
                    "session_id": session.get("session_id"),
                    "rule_code": session.get("rule_code"),
                    "story_code": session.get("story_code"),
                    "player_name": session.get("player_name"),
                    "turn_id": meta.get("turn_id", 0),
                }
            )
        except Exception:
            continue
    return entries


def _serialize_session(record: SessionRecord) -> dict[str, Any]:
    return {
        "session_id": record.session_id,
        "rule_code": record.rule_code,
        "story_code": record.story_code,
        "player_name": record.player_name,
        "language_code": record.language_code,
        "max_turns": record.max_turns,
        "turns_used": record.turns_used,
        "turns_remaining": record.turns_remaining,
        "is_finished": record.is_finished,
        "opening": record.opening,
        "state": _serialize_state_summary(record.engine.state),
        "transcript": _serialize_transcript(record),
    }


def _serialize_transcript_text(record: SessionRecord) -> str:
    player_label = "玩家"
    narrator_label = "主持人"
    normalized_language = normalize_language_code(record.language_code)
    if normalized_language == "en":
        player_label = "Player"
        narrator_label = "Narrator"
    elif normalized_language == "ja":
        player_label = "プレイヤー"
        narrator_label = "ナレーター"

    lines: list[str] = [
        f"Session ID: {record.session_id}",
        f"Rule: {record.rule_code}",
        f"Story: {record.story_code}",
        f"Player: {record.player_name}",
        "",
    ]
    for item in record.transcript:
        role = str(item.get("role", "")).strip()
        if role not in {"player", "ai"}:
            continue
        speaker = player_label if role == "player" else narrator_label
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"[{speaker}]")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _serialize_save_payload(record: SessionRecord) -> dict[str, Any]:
    return {
        "version": 2,
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "session": {
            "session_id": record.session_id,
            "rule_code": record.rule_code,
            "story_code": record.story_code,
            "player_name": record.player_name,
            "language_code": record.language_code,
            "max_turns": record.max_turns,
            "opening": record.opening,
            "created_at": record.created_at,
            "options": asdict(record.options),
            "state": record.engine.state.model_dump(mode="python"),
            "transcript": _serialize_transcript(record),
        },
    }


def _serialize_session(record: SessionRecord) -> dict[str, Any]:
    return {
        "session_id": record.session_id,
        "rule_code": record.rule_code,
        "story_code": record.story_code,
        "player_name": record.player_name,
        "language_code": record.language_code,
        "max_turns": record.max_turns,
        "turns_used": record.turns_used,
        "turns_remaining": record.turns_remaining,
        "is_finished": record.is_finished,
        "opening": record.opening,
        "state": _serialize_state_summary(record.engine.state),
        "transcript": _serialize_transcript(record),
    }


class TRPGServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        preference_path: str | None,
        registry_path: str | None,
    ) -> None:
        super().__init__(server_address, handler)
        self.repo = PromptRepository()
        self.sessions = SessionStore()
        self.catalog = _build_catalog(self.repo)
        self.language_options = build_language_options_payload()
        self.preference_path = preference_path
        self.registry_path = registry_path


class TRPGHandler(BaseHTTPRequestHandler):
    server_version = "AI-TRPG/0.1"

    @property
    def app(self) -> TRPGServer:
        return self.server  # type: ignore[return-value]

    def do_OPTIONS(self) -> None:
        _json_response(self, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/health"):
            _json_response(
                self,
                {
                    "ok": True,
                    "service": "ai-trpg",
                    "catalog_size": len(self.app.catalog),
                    "session_count": len(self.app.sessions._sessions),
                },
            )
            return

        if path == "/api/trpg/catalog":
            _json_response(self, {"rules": self.app.catalog, "languages": self.app.language_options})
            return

        if len(parts := [part for part in path.split("/") if part]) == 4 and parts[:3] == ["api", "trpg", "language"]:
            _json_response(self, get_language_pack_payload(parts[3]))
            return

        if path == "/api/trpg/saves":
            _json_response(self, {"saves": _list_save_entries()})
            return

        parts = [part for part in path.split("/") if part]
        if len(parts) == 6 and parts[:3] == ["api", "trpg", "session"] and parts[4:] == ["history", "export"]:
            self._export_history(parts[3])
            return

        if len(parts) == 4 and parts[:3] == ["api", "trpg", "session"] and parts[3]:
            record = self.app.sessions.get(parts[3])
            if record is None:
                _json_response(self, {"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            with record.lock:
                _json_response(self, _serialize_session(record))
            return

        _json_response(self, {"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        parts = [part for part in path.split("/") if part]

        try:
            if path == "/api/trpg/load":
                self._load_session()
                return

            if path == "/api/trpg/session/stream":
                self._stream_create_session()
                return

            if path == "/api/trpg/session":
                self._create_session()
                return

            if len(parts) == 5 and parts[:3] == ["api", "trpg", "session"] and parts[4] == "save":
                self._save_session(parts[3])
                return

            if len(parts) == 5 and parts[:3] == ["api", "trpg", "session"] and parts[4] == "turn":
                self._run_turn(parts[3])
                return

            if len(parts) == 6 and parts[:3] == ["api", "trpg", "session"] and parts[4:] == ["turn", "stream"]:
                self._stream_turn(parts[3])
                return

            _json_response(self, {"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)
        except FileNotFoundError as exc:
            _json_response(self, {"error": f"Scenario file not found: {exc}"}, status=HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            _json_response(self, {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        parts = [part for part in path.split("/") if part]

        if len(parts) != 4 or parts[:3] != ["api", "trpg", "session"]:
            _json_response(self, {"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)
            return

        record = self.app.sessions.delete(parts[3])
        if record is None:
            _json_response(self, {"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
            return

        record.engine.shutdown(wait=False)
        _json_response(self, {"ok": True, "session_id": parts[3]})

    def _create_session(self) -> None:
        payload = _read_json_request(self)
        rule_code = _coerce_str(payload.get("rule_code"), default="DET").upper()
        story_code = _coerce_str(payload.get("story_code"), default="")
        player_name = _coerce_str(payload.get("player_name"), default="玩家")
        max_turns = _coerce_int(payload.get("max_turns"), default=12, minimum=1, maximum=200)

        if not story_code:
            raise ValueError("story_code is required")

        options = RuntimeOptions(
            max_dialogue_window=_coerce_int(payload.get("max_dialogue_window"), default=5, minimum=1, maximum=20)
        )
        engine = MinimalTRPGEngine.from_prompt_files(
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            preference_path=self.app.preference_path,
            registry_path=self.app.registry_path,
            options=options,
        )
        opening = engine.generate_opening()
        try:
            engine.initialize_opening_npc_manager(opening)
        except Exception:
            # Keep session creation resilient if opening-time NPC initialization fails.
            pass
        session_id = uuid.uuid4().hex[:12]
        record = SessionRecord(
            session_id=session_id,
            engine=engine,
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            max_turns=max_turns,
            opening=opening,
            created_at=time.time(),
            lock=threading.RLock(),
            options=options,
            transcript=[
                {
                    "role": "ai",
                    "content": opening,
                    "created_at": time.time(),
                }
            ],
        )
        self.app.sessions.create(record)
        _json_response(self, _serialize_session(record), status=HTTPStatus.CREATED)

    def _stream_create_session(self) -> None:
        payload = _read_json_request(self)
        rule_code = _coerce_str(payload.get("rule_code"), default="DET").upper()
        story_code = _coerce_str(payload.get("story_code"), default="")
        player_name = _coerce_str(payload.get("player_name"), default="玩家")
        max_turns = _coerce_int(payload.get("max_turns"), default=12, minimum=1, maximum=200)

        if not story_code:
            raise ValueError("story_code is required")

        _stream_response_start(self)
        _stream_response_write(
            self,
            {
                "event": "runtime_log",
                "phase": "session",
                "stage": "load_scenario",
                "message": f"[Session] Loading {rule_code}/{story_code}...",
            },
        )

        options = RuntimeOptions(
            max_dialogue_window=_coerce_int(payload.get("max_dialogue_window"), default=5, minimum=1, maximum=20)
        )
        engine = MinimalTRPGEngine.from_prompt_files(
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            preference_path=self.app.preference_path,
            registry_path=self.app.registry_path,
            options=options,
        )

        try:
            opening = engine.generate_opening(
                event_callback=lambda event: _stream_response_write(
                    self,
                    {
                        "event": "runtime_log",
                        "phase": event.phase,
                        "stage": event.stage,
                        "message": event.message,
                    },
                )
            )
            try:
                opening_npc_result = engine.initialize_opening_npc_manager(
                    opening,
                    event_callback=lambda event: _stream_response_write(
                        self,
                        {
                            "event": "runtime_log",
                            "phase": event.phase,
                            "stage": event.stage,
                            "message": event.message,
                        },
                    ),
                )
                _stream_response_write(
                    self,
                    {
                        "event": "agent_update",
                        "agent_name": "npc_manager",
                        "payload": opening_npc_result.model_dump(mode="python"),
                    },
                )
            except Exception:
                opening_npc_result = None
            session_id = uuid.uuid4().hex[:12]
            record = SessionRecord(
                session_id=session_id,
                engine=engine,
                rule_code=rule_code,
                story_code=story_code,
                player_name=player_name,
                max_turns=max_turns,
                opening=opening,
                created_at=time.time(),
                lock=threading.RLock(),
                options=options,
                transcript=[
                    {
                        "role": "ai",
                        "content": opening,
                        "created_at": time.time(),
                    }
                ],
            )
            self.app.sessions.create(record)
            _stream_response_write(
                self,
                {
                    "event": "session_ready",
                    "session": _serialize_session(record),
                },
            )
        except Exception as exc:
            engine.shutdown(wait=False)
            _stream_response_write(
                self,
                {
                    "event": "error",
                    "error": str(exc),
                },
            )

    def _run_turn(self, session_id: str) -> None:
        record = self.app.sessions.get(session_id)
        if record is None:
            _json_response(self, {"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
            return

        payload = _read_json_request(self)
        player_text = _coerce_str(payload.get("player_text"), default="")
        if not player_text:
            raise ValueError("player_text is required")

        with record.lock:
            if record.is_finished:
                _json_response(
                    self,
                    {
                        "error": "Session has reached max_turns",
                        "session": _serialize_session(record),
                    },
                    status=HTTPStatus.CONFLICT,
                )
                return

            result = record.engine.run_turn(player_text, background_director=True)
            record.transcript.extend(
                [
                    {"role": "player", "content": player_text, "created_at": time.time()},
                    {"role": "ai", "content": result.narration, "created_at": time.time()},
                ]
            )
            _json_response(
                self,
                {
                    "session": _serialize_session(record),
                    "turn": {
                        "player_text": player_text,
                        "narration": result.narration,
                        "action": result.action.model_dump(mode="python"),
                        "turn_id": result.state.meta.turn_id,
                        "dicer_result": result.dicer_result.model_dump(mode="python"),
                        "npc_result": result.npc_result.model_dump(mode="python"),
                        "director_state_used": result.director_state_used.model_dump(mode="python"),
                        "next_director_result": result.next_director_result.model_dump(mode="python")
                        if result.next_director_result is not None
                        else None,
                    },
                },
            )

    def _stream_turn(self, session_id: str) -> None:
        record = self.app.sessions.get(session_id)
        if record is None:
            _json_response(self, {"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
            return

        payload = _read_json_request(self)
        player_text = _coerce_str(payload.get("player_text"), default="")
        if not player_text:
            raise ValueError("player_text is required")

        with record.lock:
            if record.is_finished:
                _json_response(
                    self,
                    {
                        "error": "Session has reached max_turns",
                        "session": _serialize_session(record),
                    },
                    status=HTTPStatus.CONFLICT,
                )
                return

            _stream_response_start(self)
            _stream_response_write(
                self,
                {
                    "event": "turn_start",
                    "player_text": player_text,
                    "session_id": record.session_id,
                },
            )

            try:
                for event in record.engine.stream_turn(player_text, background_director=False):
                    if event.event == "agent_update":
                        _stream_response_write(
                            self,
                            {
                                "event": "agent_update",
                                "agent_name": event.agent_name,
                                "payload": event.payload,
                            },
                        )
                        continue

                    if event.event == "narration_chunk":
                        _stream_response_write(
                            self,
                            {
                                "event": "narration_chunk",
                                "delta": event.delta,
                            },
                        )
                        continue

                    if event.event == "turn_result" and event.result is not None:
                        record.transcript.extend(
                            [
                                {"role": "player", "content": player_text, "created_at": time.time()},
                                {"role": "ai", "content": event.result.narration, "created_at": time.time()},
                            ]
                        )
                        _stream_response_write(
                            self,
                            {
                                "event": "turn_result",
                                "session": _serialize_session(record),
                                "turn": {
                                    "player_text": player_text,
                                    "narration": event.result.narration,
                                    "action": event.result.action.model_dump(mode="python"),
                                    "turn_id": event.result.state.meta.turn_id,
                                    "dicer_result": event.result.dicer_result.model_dump(mode="python"),
                                    "npc_result": event.result.npc_result.model_dump(mode="python"),
                                    "director_state_used": event.result.director_state_used.model_dump(mode="python"),
                                    "next_director_result": event.result.next_director_result.model_dump(mode="python")
                                    if event.result.next_director_result is not None
                                    else None,
                                },
                            },
                        )
            except Exception as exc:
                _stream_response_write(
                    self,
                    {
                        "event": "error",
                        "error": str(exc),
                    },
                )
                return

    def _save_session(self, session_id: str) -> None:
        record = self.app.sessions.get(session_id)
        if record is None:
            _json_response(self, {"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
            return

        with record.lock:
            file_name = _build_save_name(record, extension="json")
            path = SESSION_SAVE_DIR / file_name
            _write_json_file(path, _serialize_save_payload(record))
            _json_response(
                self,
                {
                    "ok": True,
                    "file_name": file_name,
                    "path": str(path),
                },
            )

    def _load_session(self) -> None:
        payload = _read_json_request(self)
        file_name = _coerce_str(payload.get("file_name"), default="")
        if not file_name:
            raise ValueError("file_name is required")

        path = SESSION_SAVE_DIR / Path(file_name).name
        if not path.exists():
            _json_response(self, {"error": "Save file not found"}, status=HTTPStatus.NOT_FOUND)
            return

        raw = json.loads(path.read_text(encoding="utf-8"))
        session_payload = raw.get("session", {})
        rule_code = str(session_payload.get("rule_code", "")).upper()
        story_code = str(session_payload.get("story_code", "")).strip()
        player_name = str(session_payload.get("player_name", "玩家")).strip() or "玩家"
        max_turns = int(session_payload.get("max_turns", 12))
        opening = str(session_payload.get("opening", "")).strip()
        options = RuntimeOptions(**dict(session_payload.get("options", {})))
        state = GameState.model_validate(session_payload.get("state", {}))
        transcript = list(session_payload.get("transcript", []))

        repo = self.app.repo
        scenario = repo.load_scenario(rule_code=rule_code, story_code=story_code)
        engine = MinimalTRPGEngine(
            scenario=scenario,
            state=state,
            prompt_repository=repo,
            preference_path=self.app.preference_path,
            registry_path=self.app.registry_path,
            options=options,
        )
        session_id = str(session_payload.get("session_id") or uuid.uuid4().hex[:12])
        record = SessionRecord(
            session_id=session_id,
            engine=engine,
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            max_turns=max_turns,
            opening=opening,
            created_at=float(session_payload.get("created_at", time.time())),
            lock=threading.RLock(),
            options=options,
            transcript=transcript,
        )
        self.app.sessions.create(record)
        _json_response(self, _serialize_session(record))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/health"):
            _json_response(
                self,
                {
                    "ok": True,
                    "service": "ai-trpg",
                    "catalog_size": len(self.app.catalog),
                    "session_count": len(self.app.sessions._sessions),
                },
            )
            return

        if path == "/api/trpg/catalog":
            _json_response(self, {"rules": self.app.catalog, "languages": self.app.language_options})
            return

        if path == "/api/trpg/saves":
            _json_response(self, {"saves": _list_save_entries()})
            return

        parts = [part for part in path.split("/") if part]
        if len(parts) == 6 and parts[:3] == ["api", "trpg", "session"] and parts[4:] == ["history", "export"]:
            self._export_history(parts[3])
            return

        if len(parts) == 4 and parts[:3] == ["api", "trpg", "session"] and parts[3]:
            record = self.app.sessions.get(parts[3])
            if record is None:
                _json_response(self, {"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            with record.lock:
                _json_response(self, _serialize_session(record))
            return

        _json_response(self, {"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def _create_session(self) -> None:
        payload = _read_json_request(self)
        rule_code = _coerce_str(payload.get("rule_code"), default="DET").upper()
        story_code = _coerce_str(payload.get("story_code"), default="")
        player_name = _coerce_str(payload.get("player_name"), default="玩家")
        language_code = normalize_language_code(_coerce_str(payload.get("language_code"), default="zh-CN"))
        max_turns = _coerce_int(payload.get("max_turns"), default=12, minimum=1, maximum=200)

        if not story_code:
            raise ValueError("story_code is required")

        options = RuntimeOptions(
            max_dialogue_window=_coerce_int(payload.get("max_dialogue_window"), default=5, minimum=1, maximum=20)
        )
        engine = MinimalTRPGEngine.from_prompt_files(
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            preference_path=self.app.preference_path,
            registry_path=self.app.registry_path,
            options=options,
            language_code=language_code,
        )
        opening = engine.generate_opening()
        try:
            engine.initialize_opening_npc_manager(opening)
        except Exception:
            pass
        session_id = uuid.uuid4().hex[:12]
        record = SessionRecord(
            session_id=session_id,
            engine=engine,
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            language_code=language_code,
            max_turns=max_turns,
            opening=opening,
            created_at=time.time(),
            lock=threading.RLock(),
            options=options,
            transcript=[{"role": "ai", "content": opening, "created_at": time.time()}],
        )
        self.app.sessions.create(record)
        _json_response(self, _serialize_session(record), status=HTTPStatus.CREATED)

    def _stream_create_session(self) -> None:
        payload = _read_json_request(self)
        rule_code = _coerce_str(payload.get("rule_code"), default="DET").upper()
        story_code = _coerce_str(payload.get("story_code"), default="")
        player_name = _coerce_str(payload.get("player_name"), default="玩家")
        language_code = normalize_language_code(_coerce_str(payload.get("language_code"), default="zh-CN"))
        max_turns = _coerce_int(payload.get("max_turns"), default=12, minimum=1, maximum=200)

        if not story_code:
            raise ValueError("story_code is required")

        _stream_response_start(self)
        _stream_response_write(
            self,
            {
                "event": "runtime_log",
                "phase": "session",
                "stage": "load_scenario",
                "message": f"[Session] Loading {rule_code}/{story_code}...",
            },
        )

        options = RuntimeOptions(
            max_dialogue_window=_coerce_int(payload.get("max_dialogue_window"), default=5, minimum=1, maximum=20)
        )
        engine = MinimalTRPGEngine.from_prompt_files(
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            preference_path=self.app.preference_path,
            registry_path=self.app.registry_path,
            options=options,
            language_code=language_code,
        )

        try:
            opening = engine.generate_opening(
                event_callback=lambda event: _stream_response_write(
                    self,
                    {
                        "event": "runtime_log",
                        "phase": event.phase,
                        "stage": event.stage,
                        "message": event.message,
                    },
                )
            )
            try:
                opening_npc_result = engine.initialize_opening_npc_manager(
                    opening,
                    event_callback=lambda event: _stream_response_write(
                        self,
                        {
                            "event": "runtime_log",
                            "phase": event.phase,
                            "stage": event.stage,
                            "message": event.message,
                        },
                    ),
                )
                _stream_response_write(
                    self,
                    {
                        "event": "agent_update",
                        "agent_name": "npc_manager",
                        "payload": opening_npc_result.model_dump(mode="python"),
                    },
                )
            except Exception:
                pass
            session_id = uuid.uuid4().hex[:12]
            record = SessionRecord(
                session_id=session_id,
                engine=engine,
                rule_code=rule_code,
                story_code=story_code,
                player_name=player_name,
                language_code=language_code,
                max_turns=max_turns,
                opening=opening,
                created_at=time.time(),
                lock=threading.RLock(),
                options=options,
                transcript=[{"role": "ai", "content": opening, "created_at": time.time()}],
            )
            self.app.sessions.create(record)
            _stream_response_write(self, {"event": "session_ready", "session": _serialize_session(record)})
        except Exception as exc:
            engine.shutdown(wait=False)
            _stream_response_write(self, {"event": "error", "error": str(exc)})

    def _load_session(self) -> None:
        payload = _read_json_request(self)
        file_name = _coerce_str(payload.get("file_name"), default="")
        if not file_name:
            raise ValueError("file_name is required")

        path = SESSION_SAVE_DIR / Path(file_name).name
        if not path.exists():
            _json_response(self, {"error": "Save file not found"}, status=HTTPStatus.NOT_FOUND)
            return

        raw = json.loads(path.read_text(encoding="utf-8"))
        session_payload = raw.get("session", {})
        rule_code = str(session_payload.get("rule_code", "")).upper()
        story_code = str(session_payload.get("story_code", "")).strip()
        player_name = str(session_payload.get("player_name", "玩家")).strip() or "玩家"
        language_code = normalize_language_code(str(session_payload.get("language_code", "zh-CN")).strip() or "zh-CN")
        max_turns = int(session_payload.get("max_turns", 12))
        opening = str(session_payload.get("opening", "")).strip()
        options = RuntimeOptions(**dict(session_payload.get("options", {})))
        state = GameState.model_validate(session_payload.get("state", {}))
        transcript = list(session_payload.get("transcript", []))

        repo = self.app.repo
        scenario = repo.load_scenario(rule_code=rule_code, story_code=story_code, language_code=language_code)
        engine = MinimalTRPGEngine(
            scenario=scenario,
            state=state,
            prompt_repository=repo,
            preference_path=self.app.preference_path,
            registry_path=self.app.registry_path,
            options=options,
            language_code=language_code,
        )
        session_id = str(session_payload.get("session_id") or uuid.uuid4().hex[:12])
        record = SessionRecord(
            session_id=session_id,
            engine=engine,
            rule_code=rule_code,
            story_code=story_code,
            player_name=player_name,
            language_code=language_code,
            max_turns=max_turns,
            opening=opening,
            created_at=float(session_payload.get("created_at", time.time())),
            lock=threading.RLock(),
            options=options,
            transcript=transcript,
        )
        self.app.sessions.create(record)
        _json_response(self, _serialize_session(record))

    def _export_history(self, session_id: str) -> None:
        record = self.app.sessions.get(session_id)
        if record is None:
            _json_response(self, {"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
            return

        with record.lock:
            content = _serialize_transcript_text(record)
            file_name = _build_save_name(record, extension="txt")
            path = HISTORY_EXPORT_DIR / file_name
            _write_text_file(path, content)

            body = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{file_name}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local HTTP server for AI TRPG direct play.")
    parser.add_argument("--host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--port", type=int, default=8788, help="Listen port")
    parser.add_argument("--preference-path", default=None, help="Preference JSON path override")
    parser.add_argument("--registry-path", default=None, help="Model registry YAML path override")
    args = parser.parse_args()

    server = TRPGServer(
        (args.host, args.port),
        TRPGHandler,
        preference_path=args.preference_path,
        registry_path=args.registry_path,
    )

    print(f"TRPG server listening on http://{args.host}:{args.port}")
    print("Endpoints:")
    print("  GET    /api/trpg/catalog")
    print("  GET    /api/trpg/saves")
    print("  POST   /api/trpg/session")
    print("  POST   /api/trpg/session/stream")
    print("  POST   /api/trpg/load")
    print("  GET    /api/trpg/session/<session_id>")
    print("  POST   /api/trpg/session/<session_id>/save")
    print("  POST   /api/trpg/session/<session_id>/turn")
    print("  POST   /api/trpg/session/<session_id>/turn/stream")
    print("  GET    /api/trpg/session/<session_id>/history/export")
    print("  DELETE /api/trpg/session/<session_id>")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
