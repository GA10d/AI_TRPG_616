from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


API_URL_TEMPLATE = "{base_url}/images/generations"


@dataclass(frozen=True)
class SeedreamCallDiagnostics:
    model: str
    prompt_chars: int
    reference_count: int
    attempt_count: int
    elapsed_seconds: float
    response_status_code: int | None = None


def _log(message: str) -> None:
    print(message)


def _guess_mime_from_path(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "image/png"


def _to_base64_image(image: Any) -> str:
    if isinstance(image, (str, Path)):
        path = Path(image)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        return base64.b64encode(path.read_bytes()).decode("ascii")

    if isinstance(image, dict):
        if "path" in image:
            return _to_base64_image(image["path"])
        if "data_url" in image:
            data_url = image["data_url"]
            if not isinstance(data_url, str) or "," not in data_url:
                raise ValueError("reference image data_url must be valid")
            return data_url.split(",", 1)[1]
        if "bytes_base64" in image:
            payload = image["bytes_base64"]
            if not isinstance(payload, str):
                raise TypeError("reference image bytes_base64 must be str")
            return payload

    raise TypeError("reference image must be a file path, or a dict with path/data_url/bytes_base64")


def _format_http_error(resp: requests.Response) -> str:
    status = resp.status_code
    try:
        payload = resp.json()
        message = payload.get("error", {}).get("message") or payload.get("message") or json.dumps(payload, ensure_ascii=False)
    except Exception:
        message = (resp.text or "").strip()
    if len(message) > 600:
        message = message[:600] + "..."
    return f"HTTP {status}: {message or '<empty response body>'}"


def _should_retry_http(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def _extract_image(json_data: dict[str, Any]) -> tuple[bytes, str] | None:
    data = json_data.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            b64 = item.get("b64_json")
            if isinstance(b64, str) and b64:
                return base64.b64decode(b64), "image/png"
    return None


def _call_seedream(
    *,
    prompt: str,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
    retries: int,
    backoff: float,
    reference_images: list[Any] | None = None,
    debug: bool = False,
) -> tuple[dict[str, Any], SeedreamCallDiagnostics]:
    url = API_URL_TEMPLATE.format(base_url=base_url.rstrip("/"))
    images = [_to_base64_image(image) for image in reference_images or []]
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "response_format": "b64_json",
        "size": "1024x1024",
    }
    if images:
        payload["image"] = images

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_exc: Exception | None = None
    attempts = max(1, retries)
    started_at = time.perf_counter()
    last_status_code: int | None = None

    if debug:
        _log(
            f"[seedream-image] model={model} prompt_chars={len(prompt)} "
            f"reference_count={len(images)} retries={attempts} read_timeout={timeout}s"
        )

    for attempt in range(1, attempts + 1):
        try:
            attempt_started_at = time.perf_counter()
            response = requests.post(url, headers=headers, json=payload, timeout=(20, timeout))
            attempt_elapsed = time.perf_counter() - attempt_started_at
            last_status_code = response.status_code
            if response.status_code >= 400:
                msg = _format_http_error(response)
                if attempt < attempts and _should_retry_http(response.status_code):
                    wait_s = backoff * (2 ** (attempt - 1))
                    _log(
                        f"[attempt {attempt}/{attempts}] status={response.status_code} "
                        f"elapsed={attempt_elapsed:.2f}s {msg} -> retry in {wait_s:.1f}s"
                    )
                    time.sleep(wait_s)
                    continue
                raise RuntimeError(msg)

            total_elapsed = time.perf_counter() - started_at
            if debug:
                _log(
                    f"[attempt {attempt}/{attempts}] status={response.status_code} "
                    f"elapsed={attempt_elapsed:.2f}s total={total_elapsed:.2f}s success"
                )
            return (
                response.json(),
                SeedreamCallDiagnostics(
                    model=model,
                    prompt_chars=len(prompt),
                    reference_count=len(images),
                    attempt_count=attempt,
                    elapsed_seconds=total_elapsed,
                    response_status_code=response.status_code,
                ),
            )
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            attempt_elapsed = time.perf_counter() - attempt_started_at
            if attempt < attempts:
                wait_s = backoff * (2 ** (attempt - 1))
                _log(
                    f"[attempt {attempt}/{attempts}] elapsed={attempt_elapsed:.2f}s "
                    f"RequestException: {exc} -> retry in {wait_s:.1f}s"
                )
                time.sleep(wait_s)
                continue
            break

    total_elapsed = time.perf_counter() - started_at
    if last_exc is None:
        raise RuntimeError(
            f"Seedream request failed after {attempts} attempts in {total_elapsed:.2f}s with status={last_status_code}."
        )
    raise RuntimeError(
        f"Seedream request failed after {attempts} attempts in {total_elapsed:.2f}s. Last error: {last_exc}"
    )
