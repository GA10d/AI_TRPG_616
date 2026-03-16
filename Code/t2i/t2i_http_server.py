from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]
TEXT_MODEL_DIR = ROOT_DIR / "Code" / "text_model"
if str(TEXT_MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(TEXT_MODEL_DIR))

from gemini_image_preview_test import _call_gemini, _extract_image, _pick_api_key


DEFAULT_MODEL = "gemini-3.1-flash-image-preview"
OUTPUT_DIR = ROOT_DIR / "outputs" / "t2i"


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(data)


def _normalize_characters(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        appearance = str(item.get("appearance", "")).strip()
        if not name or not appearance:
            continue
        result.append({"name": name, "appearance": appearance})
    return result


def _build_cast_clause(characters: list[dict[str, str]]) -> str:
    if not characters:
        return ""
    joined = "; ".join(f"{c['name']}({c['appearance']})" for c in characters)
    return f"Keep recurring character consistency. Include these characters in-frame where suitable: {joined}."


def _build_prompt(base_prompt: str, trigger: str, theme: str, characters: list[dict[str, str]]) -> str:
    style_map = {
        "parchment": "realistic travel-journal illustration, earthy tone, cinematic natural light",
        "nightwatch": "dark tactical control room style, sharp contour, low-key contrast",
        "neon": "high contrast neon cyberpunk style, dramatic perspective, dense atmosphere",
    }
    style = style_map.get(theme, style_map["parchment"])
    cast_clause = _build_cast_clause(characters)

    if trigger == "character_portrait":
        return f"TRPG player character portrait sheet, vertical composition, portrait orientation 2:3, full body, clean silhouette, {base_prompt}, {style}, no text, no watermark"
    if trigger == "npc_intro":
        return f"TRPG NPC portrait card, half body, {base_prompt}, {cast_clause} {style}, no text, no watermark"
    if trigger == "scene_shift":
        return f"TRPG exploration environment wide shot, {base_prompt}, {cast_clause} {style}, no text, no watermark"
    return f"{base_prompt}, {cast_clause} TRPG scene art, {style}, no text, no watermark"


def _sha(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _save_image(image_bytes: bytes, mime: str, cache_key: str) -> Path:
    suffix = ".png"
    if mime == "image/jpeg":
        suffix = ".jpg"
    elif mime == "image/webp":
        suffix = ".webp"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"{cache_key}{suffix}"
    target.write_bytes(image_bytes)
    return target


def _to_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _guess_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _public_image_url(handler: BaseHTTPRequestHandler, path: Path) -> str:
    host = handler.headers.get("Host") or "127.0.0.1:8787"
    return f"http://{host}/files/{path.name}"


class T2IHandler(BaseHTTPRequestHandler):
    server_version = "TRPG-T2I/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        raw_path = parsed.path
        path = raw_path.rstrip("/")
        if path in ("", "/health"):
            _json_response(
                self,
                {
                    "ok": True,
                    "service": "trpg-t2i",
                    "method": "POST",
                    "endpoint": "/api/t2i",
                },
                status=200,
            )
            return

        if path == "/api/t2i":
            _json_response(
                self,
                {
                    "ok": True,
                    "message": "Use POST /api/t2i with JSON body.",
                    "required_fields": ["prompt"],
                    "optional_fields": ["trigger", "theme", "scene_id", "model", "characters"],
                },
                status=200,
            )
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return

        if raw_path.startswith("/files/"):
            name = unquote(raw_path[len("/files/"):]).strip()
            # Block traversal and nested paths.
            if not name or "/" in name or "\\" in name:
                _json_response(self, {"error": "Invalid file name"}, status=HTTPStatus.BAD_REQUEST)
                return
            target = OUTPUT_DIR / name
            if not target.exists() or not target.is_file():
                _json_response(self, {"error": "File not found"}, status=HTTPStatus.NOT_FOUND)
                return

            payload = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _guess_mime(target))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
            return

        _json_response(self, {"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        _json_response(self, {"ok": True}, status=200)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/api/t2i":
            _json_response(self, {"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8")) if raw else {}

            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                raise ValueError("prompt is required")

            trigger = str(body.get("trigger", "manual")).strip()
            theme = str(body.get("theme", "parchment")).strip()
            scene_id = str(body.get("scene_id", "default")).strip()
            model = str(body.get("model", self.server.model)).strip()
            characters = _normalize_characters(body.get("characters", []))

            final_prompt = _build_prompt(prompt, trigger=trigger, theme=theme, characters=characters)
            cache_key = _sha(f"{scene_id}|{trigger}|{theme}|{final_prompt}|{model}|{json.dumps(characters, ensure_ascii=False)}")

            existing = next((p for p in OUTPUT_DIR.glob(f"{cache_key}.*") if p.is_file()), None)
            if existing:
                mime = "image/png"
                if existing.suffix.lower() in (".jpg", ".jpeg"):
                    mime = "image/jpeg"
                elif existing.suffix.lower() == ".webp":
                    mime = "image/webp"
                image_data = existing.read_bytes()
                _json_response(
                    self,
                    {
                        "cached": True,
                        "provider": "gemini-http",
                        "prompt": final_prompt,
                        "mime_type": mime,
                        "image_url": _public_image_url(self, existing),
                        "image_data_url": _to_data_url(image_data, mime),
                        "output_path": str(existing.as_posix()),
                    },
                )
                return

            data = _call_gemini(
                prompt=final_prompt,
                model=model,
                api_key=self.server.api_key,
                timeout=self.server.timeout_seconds,
                retries=self.server.retries,
                backoff=self.server.backoff,
            )

            extracted = _extract_image(data)
            if extracted is None:
                raise RuntimeError("No image data returned from model.")

            image_bytes, mime = extracted
            saved = _save_image(image_bytes, mime, cache_key)
            _json_response(
                self,
                {
                    "cached": False,
                    "provider": "gemini-http",
                    "prompt": final_prompt,
                    "mime_type": mime,
                    "image_url": _public_image_url(self, saved),
                    "image_data_url": _to_data_url(image_bytes, mime),
                    "output_path": str(saved.as_posix()),
                },
            )

        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


class T2IServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        api_key: str,
        model: str,
        timeout_seconds: int,
        retries: int,
        backoff: float,
    ) -> None:
        super().__init__(server_address, handler)
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.backoff = backoff


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local HTTP server for TRPG text-to-image.")
    parser.add_argument("--host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--port", type=int, default=8787, help="Listen port")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name")
    parser.add_argument("--api-key", default=None, help="Gemini API key; fallback env GEMINI_API_KEY/GOOGLE_API_KEY")
    parser.add_argument("--timeout", type=int, default=90, help="Read timeout seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry attempts")
    parser.add_argument("--backoff", type=float, default=2.0, help="Exponential backoff start")
    args = parser.parse_args()

    api_key = _pick_api_key(args.api_key)
    server = T2IServer(
        (args.host, args.port),
        T2IHandler,
        api_key=api_key,
        model=args.model,
        timeout_seconds=args.timeout,
        retries=args.retries,
        backoff=args.backoff,
    )

    print(f"T2I server listening on http://{args.host}:{args.port}/api/t2i")
    print(f"Model: {args.model}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
