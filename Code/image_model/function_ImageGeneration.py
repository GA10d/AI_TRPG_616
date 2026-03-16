from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT_DIR / "Code"
IMAGE_MODEL_DIR = CODE_DIR / "image_model"

for path in (CODE_DIR, IMAGE_MODEL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import data.data_Path as data_path
from tools.function_Preference import PreferenceManager
from user_info.option_Imagemodel import ImageModelConfig, ImageModelRegistry

try:
    from .provider_GeminiImage import _call_gemini, _extract_image
except ImportError:
    from provider_GeminiImage import _call_gemini, _extract_image


OUTPUT_DIR = ROOT_DIR / "outputs" / "t2i"


@dataclass(frozen=True)
class GeneratedImageResult:
    image_bytes: bytes
    mime_type: str
    prompt: str
    provider: str
    model_code: str
    model_name: str
    output_path: Optional[Path] = None
    cached: bool = False
    reference_count: int = 0


@dataclass(frozen=True)
class ImagePromptConfig:
    version: int
    default_theme: str
    default_trigger: str
    fallback_trigger_template: str
    themes: dict[str, str]
    trigger_templates: dict[str, str]
    character_clause_template: str
    character_join_separator: str
    character_entry_template: str


def _resolve_code_path(path_str: str) -> Path:
    return CODE_DIR / Path(path_str)


def _get_registry(registry_path: str | Path | None = None) -> ImageModelRegistry:
    registry_file = _resolve_code_path(data_path.PATH_DATA_IMAGE_MODEL)
    if registry_path is not None:
        registry_file = Path(registry_path)
    return ImageModelRegistry.load(registry_file)


def _get_prompt_config(prompt_config_path: str | Path | None = None) -> ImagePromptConfig:
    config_file = _resolve_code_path(data_path.PATH_DATA_IMAGE_PROMPT)
    if prompt_config_path is not None:
        config_file = Path(prompt_config_path)
    if not config_file.exists():
        raise FileNotFoundError(config_file)

    data = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("Image prompt config root must be a dict")

    version = data.get("version")
    if not isinstance(version, int):
        raise TypeError("Image prompt config version must be int")

    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        raise TypeError("Image prompt config defaults must be a dict")

    default_theme = defaults.get("theme")
    default_trigger = defaults.get("trigger")
    fallback_trigger_template = defaults.get("fallback_trigger_template")
    if not isinstance(default_theme, str) or not default_theme:
        raise TypeError("Image prompt config defaults.theme must be non-empty str")
    if not isinstance(default_trigger, str) or not default_trigger:
        raise TypeError("Image prompt config defaults.trigger must be non-empty str")
    if not isinstance(fallback_trigger_template, str) or not fallback_trigger_template:
        raise TypeError("Image prompt config defaults.fallback_trigger_template must be non-empty str")

    themes_raw = data.get("themes")
    if not isinstance(themes_raw, dict) or not themes_raw:
        raise TypeError("Image prompt config themes must be a non-empty dict")
    themes: dict[str, str] = {}
    for theme_name, theme_value in themes_raw.items():
        if not isinstance(theme_value, dict):
            raise TypeError(f"Image prompt config themes.{theme_name} must be a dict")
        style = theme_value.get("style")
        if not isinstance(style, str) or not style:
            raise TypeError(f"Image prompt config themes.{theme_name}.style must be non-empty str")
        themes[str(theme_name)] = style

    triggers_raw = data.get("triggers")
    if not isinstance(triggers_raw, dict) or not triggers_raw:
        raise TypeError("Image prompt config triggers must be a non-empty dict")
    trigger_templates: dict[str, str] = {}
    for trigger_name, trigger_value in triggers_raw.items():
        if not isinstance(trigger_value, dict):
            raise TypeError(f"Image prompt config triggers.{trigger_name} must be a dict")
        template = trigger_value.get("template")
        if not isinstance(template, str) or not template:
            raise TypeError(f"Image prompt config triggers.{trigger_name}.template must be non-empty str")
        trigger_templates[str(trigger_name)] = template

    character_clause_raw = data.get("character_clause")
    if not isinstance(character_clause_raw, dict):
        raise TypeError("Image prompt config character_clause must be a dict")

    character_clause_template = character_clause_raw.get("template")
    character_join_separator = character_clause_raw.get("join_separator")
    character_entry_template = character_clause_raw.get("entry_template")
    if not isinstance(character_clause_template, str) or not character_clause_template:
        raise TypeError("Image prompt config character_clause.template must be non-empty str")
    if not isinstance(character_join_separator, str):
        raise TypeError("Image prompt config character_clause.join_separator must be str")
    if not isinstance(character_entry_template, str) or not character_entry_template:
        raise TypeError("Image prompt config character_clause.entry_template must be non-empty str")

    return ImagePromptConfig(
        version=version,
        default_theme=default_theme,
        default_trigger=default_trigger,
        fallback_trigger_template=fallback_trigger_template,
        themes=themes,
        trigger_templates=trigger_templates,
        character_clause_template=character_clause_template,
        character_join_separator=character_join_separator,
        character_entry_template=character_entry_template,
    )


def get_selected_image_model_code(
    preference_path: str | Path | None = None,
) -> str:
    pref_file = _resolve_code_path(data_path.PATH_DATA_PREFERENCE)
    if preference_path is not None:
        pref_file = Path(preference_path)

    manager = PreferenceManager(path=str(pref_file))
    image_model_pref = manager.get("image_model", {})
    if not isinstance(image_model_pref, dict):
        raise TypeError("Preference 'image_model' must be a dict")

    model_code = image_model_pref.get("code")
    if not isinstance(model_code, str) or not model_code:
        raise ValueError("Preference 'image_model.code' must be a non-empty string")

    return model_code


def get_selected_image_model_config(
    *,
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> ImageModelConfig:
    model_code = get_selected_image_model_code(preference_path=preference_path)
    registry = _get_registry(registry_path=registry_path)
    return registry.get_by_code(model_code)


def _resolve_api_key(model_config: ImageModelConfig, cli_key: Optional[str] = None) -> str:
    if cli_key:
        return cli_key

    env_candidates = [
        model_config.api_key_env,
        f"{model_config.code.upper()}_API_KEY",
        f"{model_config.name.upper().replace(' ', '_')}_API_KEY",
        f"{model_config.dependence.upper()}_API_KEY",
    ]

    if model_config.code.lower() == "gemini" or model_config.dependence == "Google":
        env_candidates.extend(["GEMINI_API_KEY", "GOOGLE_API_KEY"])

    for env_name in env_candidates:
        api_key = os.getenv(env_name)
        if api_key:
            return api_key

    raise ValueError(
        f"API key not found for image model {model_config.code!r}. "
        f"Tried env vars: {env_candidates}"
    )


def _resolve_runtime_model(
    *,
    requested_model: Optional[str],
    model_config: ImageModelConfig,
) -> str:
    if requested_model:
        return requested_model.strip()
    _, model_name = model_config.resolve_endpoint(feature="text_to_image")
    return model_name


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


def _build_cast_clause(
    characters: list[dict[str, str]],
    prompt_config: ImagePromptConfig,
) -> str:
    if not characters:
        return ""
    joined = prompt_config.character_join_separator.join(
        prompt_config.character_entry_template.format(
            name=c["name"],
            appearance=c["appearance"],
        )
        for c in characters
    )
    return prompt_config.character_clause_template.format(joined_characters=joined)


def _build_prompt(
    base_prompt: str,
    trigger: str,
    theme: str,
    characters: list[dict[str, str]],
    prompt_config: ImagePromptConfig,
) -> str:
    resolved_theme = theme or prompt_config.default_theme
    theme_style = prompt_config.themes.get(resolved_theme)
    if theme_style is None:
        theme_style = prompt_config.themes[prompt_config.default_theme]

    resolved_trigger = trigger or prompt_config.default_trigger
    trigger_template = prompt_config.trigger_templates.get(
        resolved_trigger,
        prompt_config.fallback_trigger_template,
    )
    cast_clause = _build_cast_clause(characters, prompt_config)

    return trigger_template.format(
        base_prompt=base_prompt,
        trigger=resolved_trigger,
        theme=resolved_theme,
        theme_style=theme_style,
        cast_clause=cast_clause,
    )


def _sha(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _reference_cache_tag(reference_images: Optional[list[Any]]) -> str:
    if not reference_images:
        return "no-reference"

    normalized: list[str] = []
    for item in reference_images:
        if isinstance(item, (str, Path)):
            path = Path(item)
            normalized.append(f"path:{path.resolve()}:{path.stat().st_size}:{path.stat().st_mtime_ns}")
            continue

        if isinstance(item, dict):
            if "path" in item:
                path = Path(item["path"])
                normalized.append(f"path:{path.resolve()}:{path.stat().st_size}:{path.stat().st_mtime_ns}")
                continue
            if "data_url" in item:
                normalized.append(f"data_url:{len(str(item['data_url']))}")
                continue
            if "bytes_base64" in item:
                mime_type = item.get("mime_type", "image/png")
                normalized.append(f"bytes_base64:{mime_type}:{len(str(item['bytes_base64']))}")
                continue

        normalized.append(f"unknown:{repr(item)}")

    return "|".join(normalized)


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


def generate_image(
    *,
    prompt: str,
    trigger: str = "manual",
    theme: str = "parchment",
    scene_id: str = "default",
    characters: Optional[list[dict[str, str]]] = None,
    reference_images: Optional[list[Any]] = None,
    model_code: Optional[str] = None,
    model_name: Optional[str] = None,
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    prompt_config_path: str | Path | None = None,
    api_key: Optional[str] = None,
    timeout: int = 90,
    retries: int = 3,
    backoff: float = 2.0,
    save_output: bool = True,
    use_cache: bool = True,
) -> GeneratedImageResult:
    """
    Generate an image using the configured image model.

    Parameters:
    - prompt:
      The user-facing text prompt. This is the core description of the image
      you want to generate.
    - trigger:
      The scene trigger type used by this project to rewrite prompt style.
      Common values: "manual", "scene_shift", "npc_intro", "character_portrait".
    - theme:
      Visual theme preset for prompt decoration.
      Common values: "parchment", "nightwatch", "neon".
    - scene_id:
      Stable scene identifier used to build the cache key. Reusing the same
      scene_id with the same prompt can reuse cached output.
    - characters:
      Optional recurring characters to keep visually consistent.
      Expected format: [{"name": "...", "appearance": "..."}, ...]
    - reference_images:
      Optional reference image inputs for Gemini image editing / guided generation.
      Supported formats:
      1. local file path: "F:/path/to/ref.png" or Path(...)
      2. dict with {"path": "..."}
      3. dict with {"data_url": "data:image/png;base64,..."}
      4. dict with {"bytes_base64": "...", "mime_type": "image/png"}
    - model_code:
      Optional configured model code from data_ImageModel.yml, such as "gemini".
      If omitted, the function reads image_model.code from user preferences.
    - model_name:
      Optional exact provider model name override.
      If omitted, the function uses the model resolved from the registry.
    - preference_path:
      Optional path to the user preference JSON file.
      Useful for tests or temporary preference files.
    - registry_path:
      Optional path to the image model YAML registry.
      Useful if you want to test a different registry file.
    - prompt_config_path:
      Optional path to the JSON prompt config file.
      Useful if you want to test different trigger/theme templates.
    - api_key:
      Optional API key override. If omitted, the function resolves the key
      from the configured environment variable(s).
    - timeout:
      Read timeout in seconds for the upstream image model request.
    - retries:
      Number of retry attempts for retryable failures.
    - backoff:
      Initial exponential backoff in seconds between retries.
    - save_output:
      Whether to save the generated image into outputs/t2i.
    - use_cache:
      Whether to reuse an existing file with the same computed cache key.

    Returns:
    - GeneratedImageResult:
      Contains raw image bytes, MIME type, final prompt, provider/model info,
      cache status, and saved file path when save_output=True.
    """
    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        raise ValueError("prompt is required")

    normalized_characters = _normalize_characters(characters or [])
    normalized_reference_images = list(reference_images or [])
    registry = _get_registry(registry_path=registry_path)
    prompt_config = _get_prompt_config(prompt_config_path=prompt_config_path)
    selected_model_config = (
        registry.get_by_code(model_code)
        if model_code
        else get_selected_image_model_config(
            preference_path=preference_path,
            registry_path=registry_path,
        )
    )
    runtime_model = _resolve_runtime_model(
        requested_model=model_name,
        model_config=selected_model_config,
    )
    resolved_api_key = _resolve_api_key(selected_model_config, cli_key=api_key)

    final_prompt = _build_prompt(
        cleaned_prompt,
        trigger=trigger,
        theme=theme,
        characters=normalized_characters,
        prompt_config=prompt_config,
    )
    cache_key = _sha(
        f"{scene_id}|{trigger}|{theme}|{final_prompt}|{runtime_model}|"
        f"{json.dumps(normalized_characters, ensure_ascii=False)}|"
        f"{_reference_cache_tag(normalized_reference_images)}"
    )

    existing = None
    if use_cache:
        existing = next((p for p in OUTPUT_DIR.glob(f"{cache_key}.*") if p.is_file()), None)
    if existing:
        mime = "image/png"
        if existing.suffix.lower() in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif existing.suffix.lower() == ".webp":
            mime = "image/webp"
        return GeneratedImageResult(
            image_bytes=existing.read_bytes(),
            mime_type=mime,
            prompt=final_prompt,
            provider=selected_model_config.code,
            model_code=selected_model_config.code,
            model_name=runtime_model,
            output_path=existing,
            cached=True,
            reference_count=len(normalized_reference_images),
        )

    data = _call_gemini(
        prompt=final_prompt,
        model=runtime_model,
        api_key=resolved_api_key,
        timeout=timeout,
        retries=retries,
        backoff=backoff,
        reference_images=normalized_reference_images,
    )

    extracted = _extract_image(data)
    if extracted is None:
        raise RuntimeError("No image data returned from model.")

    image_bytes, mime = extracted
    saved_path = _save_image(image_bytes, mime, cache_key) if save_output else None
    return GeneratedImageResult(
        image_bytes=image_bytes,
        mime_type=mime,
        prompt=final_prompt,
        provider=selected_model_config.code,
        model_code=selected_model_config.code,
        model_name=runtime_model,
        output_path=saved_path,
        cached=False,
        reference_count=len(normalized_reference_images),
    )


class T2IHandler(BaseHTTPRequestHandler):
    server_version = "TRPG-T2I/0.2"

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
                    "model_code": self.server.model_config.code,
                    "model_name": self.server.model,
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
                    "optional_fields": ["trigger", "theme", "scene_id", "model", "characters", "reference_images"],
                    "default_model_code": self.server.model_config.code,
                    "default_model_name": self.server.model,
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
            result = generate_image(
                prompt=prompt,
                trigger=trigger,
                theme=theme,
                scene_id=scene_id,
                characters=body.get("characters", []),
                reference_images=body.get("reference_images", []),
                model_code=self.server.model_config.code,
                model_name=body.get("model"),
                api_key=self.server.api_key,
                timeout=self.server.timeout_seconds,
                retries=self.server.retries,
                backoff=self.server.backoff,
                save_output=True,
                use_cache=True,
            )
            _json_response(
                self,
                {
                    "cached": result.cached,
                    "provider": result.provider,
                    "prompt": result.prompt,
                    "mime_type": result.mime_type,
                    "image_url": _public_image_url(self, result.output_path) if result.output_path else None,
                    "image_data_url": _to_data_url(result.image_bytes, result.mime_type),
                    "output_path": str(result.output_path.as_posix()) if result.output_path else None,
                    "reference_count": result.reference_count,
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
        model_config: ImageModelConfig,
        timeout_seconds: int,
        retries: int,
        backoff: float,
    ) -> None:
        super().__init__(server_address, handler)
        self.api_key = api_key
        self.model = model
        self.model_config = model_config
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.backoff = backoff


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local HTTP server for TRPG text-to-image.")
    parser.add_argument("--host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--port", type=int, default=8787, help="Listen port")
    parser.add_argument("--model", default=None, help="Exact provider model name override")
    parser.add_argument("--model-code", default=None, help="Configured image model code override")
    parser.add_argument(
        "--api-key",
        default=None,
        help="Image model API key override; otherwise resolve from the configured env var",
    )
    parser.add_argument("--timeout", type=int, default=90, help="Read timeout seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry attempts")
    parser.add_argument("--backoff", type=float, default=2.0, help="Exponential backoff start")
    args = parser.parse_args()

    registry = _get_registry()
    model_config = (
        registry.get_by_code(args.model_code)
        if args.model_code
        else get_selected_image_model_config()
    )
    runtime_model = _resolve_runtime_model(requested_model=args.model, model_config=model_config)
    api_key = _resolve_api_key(model_config, cli_key=args.api_key)

    server = T2IServer(
        (args.host, args.port),
        T2IHandler,
        api_key=api_key,
        model=runtime_model,
        model_config=model_config,
        timeout_seconds=args.timeout,
        retries=args.retries,
        backoff=args.backoff,
    )

    print(f"T2I server listening on http://{args.host}:{args.port}/api/t2i")
    print(f"Model code: {model_config.code}")
    print(f"Model name: {runtime_model}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
