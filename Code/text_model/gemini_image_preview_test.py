from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_MODEL = "gemini-3.1-flash-image-preview"
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _pick_api_key(cli_key: str | None) -> str:
    key = cli_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise ValueError("Missing API key. Set GEMINI_API_KEY or GOOGLE_API_KEY, or pass --api-key.")
    return key


def _build_error_hint(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "Connection timeout: cannot establish TLS connection in time. Check network/proxy/firewall."
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "Read timeout: server did not return full response in time. Increase --timeout or retry later."
    if isinstance(exc, requests.exceptions.HTTPError):
        return "HTTP error from Gemini API. Check model availability, API key permissions, and quota."
    return "Request failed. Check network/proxy and API key settings."


def _format_http_error(resp: requests.Response) -> str:
    status = resp.status_code
    try:
        payload = resp.json()
        err = payload.get("error", {})
        message = err.get("message") or json.dumps(payload, ensure_ascii=False)
    except Exception:
        message = (resp.text or "").strip()
    if len(message) > 600:
        message = message[:600] + "..."
    return f"HTTP {status}: {message or '<empty response body>'}"


def _should_retry_http(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def _call_gemini(prompt: str, model: str, api_key: str, timeout: int, retries: int, backoff: float) -> dict[str, Any]:
    url = API_URL_TEMPLATE.format(model=model)
    params = {"key": api_key}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }

    last_exc: Exception | None = None
    attempts = max(1, retries)

    for attempt in range(1, attempts + 1):
        try:
            # timeout=(connect_timeout, read_timeout)
            response = requests.post(url, params=params, json=payload, timeout=(20, timeout))
            if response.status_code >= 400:
                msg = _format_http_error(response)
                if attempt < attempts and _should_retry_http(response.status_code):
                    wait_s = backoff * (2 ** (attempt - 1))
                    _log(f"[attempt {attempt}/{attempts}] {msg} -> retry in {wait_s:.1f}s")
                    time.sleep(wait_s)
                    continue
                raise RuntimeError(msg)
            return response.json()

        except requests.exceptions.ConnectTimeout as exc:
            last_exc = exc
            if attempt < attempts:
                wait_s = backoff * (2 ** (attempt - 1))
                _log(f"[attempt {attempt}/{attempts}] ConnectTimeout: {exc} -> retry in {wait_s:.1f}s")
                time.sleep(wait_s)
                continue

        except requests.exceptions.ReadTimeout as exc:
            last_exc = exc
            if attempt < attempts:
                wait_s = backoff * (2 ** (attempt - 1))
                _log(f"[attempt {attempt}/{attempts}] ReadTimeout(read={timeout}s): {exc} -> retry in {wait_s:.1f}s")
                time.sleep(wait_s)
                continue

        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < attempts:
                wait_s = backoff * (2 ** (attempt - 1))
                _log(f"[attempt {attempt}/{attempts}] RequestException: {exc} -> retry in {wait_s:.1f}s")
                time.sleep(wait_s)
                continue

        break

    if last_exc is None:
        raise RuntimeError("Gemini call failed for unknown reason.")

    raise RuntimeError(f"Gemini request failed after {attempts} attempts. {_build_error_hint(last_exc)}\nLast error: {last_exc}")


def _extract_image(json_data: dict[str, Any]) -> tuple[bytes, str] | None:
    candidates = json_data.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                raw = base64.b64decode(inline["data"])
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                return raw, mime
    return None


def _extract_text(json_data: dict[str, Any]) -> str:
    texts: list[str] = []
    candidates = json_data.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return "\n".join(texts)


def _guess_suffix(mime: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    return mapping.get(mime.lower(), ".png")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an image with Gemini and save it locally.")
    parser.add_argument("--prompt", required=True, help="Prompt for image generation")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name")
    parser.add_argument("--output", default="outputs/gemini_image", help="Output file path (with or without extension)")
    parser.add_argument("--api-key", default=None, help="Gemini API key (fallback: GEMINI_API_KEY / GOOGLE_API_KEY)")
    parser.add_argument("--timeout", type=int, default=90, help="Read timeout seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry attempts for timeout/retryable HTTP errors")
    parser.add_argument("--backoff", type=float, default=2.0, help="Initial retry backoff seconds (exponential)")
    parser.add_argument("--dump-json", action="store_true", help="Also write full JSON response to <output>.json")
    args = parser.parse_args()

    api_key = _pick_api_key(args.api_key)

    _log(f"Calling model={args.model}, retries={args.retries}, read_timeout={args.timeout}s")
    data = _call_gemini(
        prompt=args.prompt,
        model=args.model,
        api_key=api_key,
        timeout=args.timeout,
        retries=args.retries,
        backoff=args.backoff,
    )

    output_path = Path(args.output)
    image = _extract_image(data)
    if image is None:
        text = _extract_text(data)
        raise RuntimeError(
            "Model returned no image data. Response text: " + (text or "<empty>")
        )

    image_bytes, mime = image

    if output_path.suffix == "":
        output_path = output_path.with_suffix(_guess_suffix(mime))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)

    if args.dump_json:
        output_path.with_suffix(output_path.suffix + ".json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"Saved image to: {output_path}")
    print(f"MIME type: {mime}")


if __name__ == "__main__":
    main()
