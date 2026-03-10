"""
Extensible text model helpers.

Current capabilities:
1. Get the selected model from user preferences.
2. Get a normal non-streaming reply.
3. Get a normal streaming reply.
4. Get a mini-version non-streaming reply when supported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Protocol, Sequence

from dotenv import load_dotenv
from openai import OpenAI

import data.data_Path as data_path
from tools.function_Preference import PreferenceManager
from user_info.option_TextModel import ModelConfig, ModelRegistry


Message = Dict[str, str]
CODE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ProviderRequest:
    model_config: ModelConfig
    model_name: str
    base_url: Optional[str]
    messages: Sequence[Message]
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None
    extra_options: Optional[Dict[str, Any]] = None


class ProviderAdapter(Protocol):
    def generate(self, request: ProviderRequest) -> str:
        ...

    def stream_generate(self, request: ProviderRequest) -> Iterator[str]:
        ...


class OpenAICompatibleProviderAdapter:
    """Provider adapter for OpenAI-compatible chat.completions APIs."""

    def generate(self, request: ProviderRequest) -> str:
        client = self._build_client(request.model_config, request.base_url)
        payload = self._build_payload(request)
        completion = client.chat.completions.create(**payload)
        return completion.choices[0].message.content or ""

    def stream_generate(self, request: ProviderRequest) -> Iterator[str]:
        client = self._build_client(request.model_config, request.base_url)
        payload = self._build_payload(request)
        payload["stream"] = True

        stream = client.chat.completions.create(**payload)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = getattr(chunk.choices[0].delta, "content", None)
            if delta:
                yield delta

    def _build_client(self, model_config: ModelConfig, base_url: Optional[str]) -> OpenAI:
        api_key = self._resolve_api_key(model_config)
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        return OpenAI(**client_kwargs)

    @staticmethod
    def _build_payload(request: ProviderRequest) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": request.model_name,
            "messages": list(request.messages),
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.timeout is not None:
            payload["timeout"] = request.timeout
        if request.extra_options:
            payload.update(request.extra_options)
        return payload

    @staticmethod
    def _resolve_api_key(model_config: ModelConfig) -> str:
        load_dotenv()

        env_candidates = [
            f"{model_config.code.upper()}_API_KEY",
            f"{model_config.name.upper().replace(' ', '_')}_API_KEY",
            f"{model_config.dependence.upper()}_API_KEY",
        ]

        if model_config.base_url and "deepseek" in model_config.base_url.lower():
            env_candidates.insert(0, "DEEPSEEK_API_KEY")
        if model_config.dependence == "OpenAI" and not model_config.base_url:
            env_candidates.insert(0, "OPENAI_API_KEY")

        for env_name in env_candidates:
            api_key = os.getenv(env_name)
            if api_key:
                return api_key

        raise ValueError(
            f"API key not found for model {model_config.code!r}. "
            f"Tried env vars: {env_candidates}"
        )


def _resolve_code_path(path_str: str) -> Path:
    return CODE_ROOT / Path(path_str)


def _get_registry(registry_path: str | Path | None = None) -> ModelRegistry:
    registry_file = _resolve_code_path(data_path.PATH_DATA_LLM)
    if registry_path is not None:
        registry_file = Path(registry_path)
    return ModelRegistry.load(registry_file)


def _get_provider_adapters() -> Dict[str, ProviderAdapter]:
    return {
        "OpenAI": OpenAICompatibleProviderAdapter(),
    }


def get_selected_text_model_code(
    preference_path: str | Path | None = None,
) -> str:
    pref_file = _resolve_code_path(data_path.PATH_DATA_PREFERENCE)
    if preference_path is not None:
        pref_file = Path(preference_path)

    manager = PreferenceManager(path=str(pref_file))
    text_model_pref = manager.get("text_model", {})
    if not isinstance(text_model_pref, dict):
        raise TypeError("Preference 'text_model' must be a dict")

    model_code = text_model_pref.get("code")
    if not isinstance(model_code, str) or not model_code:
        raise ValueError("Preference 'text_model.code' must be a non-empty string")

    return model_code


def get_selected_text_model_config(
    *,
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> ModelConfig:
    model_code = get_selected_text_model_code(preference_path=preference_path)
    registry = _get_registry(registry_path=registry_path)
    return registry.get_by_code(model_code)


def _build_request(
    *,
    input: Sequence[Message],
    feature: Optional[str] = None,
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
    extra_options: Optional[Dict[str, Any]] = None,
) -> ProviderRequest:
    if not input:
        raise ValueError("input must not be empty")

    model_config = get_selected_text_model_config(
        preference_path=preference_path,
        registry_path=registry_path,
    )
    base_url, model_name = model_config.resolve_endpoint(feature=feature)

    return ProviderRequest(
        model_config=model_config,
        model_name=model_name,
        base_url=base_url,
        messages=input,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_options=extra_options,
    )


def _get_adapter(model_config: ModelConfig) -> ProviderAdapter:
    adapters = _get_provider_adapters()
    adapter = adapters.get(model_config.dependence)
    if adapter is None:
        raise ValueError(
            f"No provider adapter registered for dependence={model_config.dependence!r}"
        )
    return adapter


def get_normal_reply(
    *,
    input: Sequence[Message],
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
    extra_options: Optional[Dict[str, Any]] = None,
) -> str:
    request = _build_request(
        input=input,
        preference_path=preference_path,
        registry_path=registry_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_options=extra_options,
    )
    return _get_adapter(request.model_config).generate(request)


def get_stream_reply(
    *,
    input: Sequence[Message],
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
    extra_options: Optional[Dict[str, Any]] = None,
) -> Iterator[str]:
    request = _build_request(
        input=input,
        preference_path=preference_path,
        registry_path=registry_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_options=extra_options,
    )
    return _get_adapter(request.model_config).stream_generate(request)


def get_reply(
    *,
    is_stream: bool = True,
    input: Sequence[Message],
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
    extra_options: Optional[Dict[str, Any]] = None,
    is_strem: Optional[bool] = None,
) -> str | Iterator[str]:
    if is_strem is not None:
        is_stream = is_strem

    if is_stream:
        return get_stream_reply(
            input=input,
            preference_path=preference_path,
            registry_path=registry_path,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_options=extra_options,
        )

    return get_normal_reply(
        input=input,
        preference_path=preference_path,
        registry_path=registry_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_options=extra_options,
    )


def get_mini_reply(
    *,
    input: Sequence[Message],
    preference_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
    extra_options: Optional[Dict[str, Any]] = None,
) -> str:
    request = _build_request(
        input=input,
        feature="mini_version",
        preference_path=preference_path,
        registry_path=registry_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_options=extra_options,
    )
    return _get_adapter(request.model_config).generate(request)


def collect_stream_text(chunks: Iterable[str]) -> str:
    return "".join(chunks)
