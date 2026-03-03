from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from openai import OpenAI

from user_info.option_textmodel import ModelRegistry


Message = Dict[str, str]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model_code: str
    model_name: str
    provider: str
    feature: Optional[str] = None
    raw: Any = None


class ProviderAdapter(Protocol):
    def generate(
        self,
        *,
        model_name: str,
        base_url: Optional[str],
        messages: Sequence[Message],
        temperature: float,
        max_tokens: Optional[int],
        timeout: Optional[float],
        extra_options: Optional[Dict[str, Any]],
    ) -> LLMResponse:
        ...


class OpenAIProviderAdapter:
    """Adapter for OpenAI-compatible chat.completions APIs."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def generate(
        self,
        *,
        model_name: str,
        base_url: Optional[str],
        messages: Sequence[Message],
        temperature: float,
        max_tokens: Optional[int],
        timeout: Optional[float],
        extra_options: Optional[Dict[str, Any]],
    ) -> LLMResponse:
        
        client_kwargs: Dict[str, Any] = {"api_key": self._api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": list(messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if timeout is not None:
            payload["timeout"] = timeout
        if extra_options:
            payload.update(extra_options)

        completion = client.chat.completions.create(**payload)
        text = completion.choices[0].message.content or ""

        return LLMResponse(
            text=text,
            model_code="",
            model_name=model_name,
            provider="OpenAI",
            raw=completion,
        )


class LLMClient:
    """
    Unified LLM caller based on ModelRegistry.

    Extension path:
    1) Implement a new ProviderAdapter.
    2) register_provider("Anthropic", your_adapter_instance).
    3) Set `dependence: "Anthropic"` in text_models.yml.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        provider_adapters: Dict[str, ProviderAdapter],
    ):
        self._registry = registry
        self._provider_adapters = dict(provider_adapters)

    @classmethod
    def from_config(
        cls,
        *,
        config_path: str | Path,
        openai_api_key: str,
    ) -> "LLMClient":
        registry = ModelRegistry.load(config_path)
        providers: Dict[str, ProviderAdapter] = {
            "OpenAI": OpenAIProviderAdapter(api_key=openai_api_key),
        }
        return cls(registry=registry, provider_adapters=providers)

    def register_provider(self, dependence: str, adapter: ProviderAdapter) -> None:
        self._provider_adapters[dependence] = adapter

    def chat(
        self,
        *,
        model_code: str,
        messages: Sequence[Message],
        feature: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        extra_options: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        model_cfg = self._registry.get_by_code(model_code)
        self._validate_messages(messages)

        base_url, model_name = model_cfg.resolve_endpoint(feature=feature)
        adapter = self._provider_adapters.get(model_cfg.dependence)
        if adapter is None:
            raise ValueError(
                f"No provider adapter for dependence={model_cfg.dependence!r}. "
                "Use register_provider(...) to add one."
            )

        resp = adapter.generate(
            model_name=model_name,
            base_url=base_url,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_options=extra_options,
        )

        return LLMResponse(
            text=resp.text,
            model_code=model_cfg.code,
            model_name=model_name,
            provider=model_cfg.dependence,
            feature=feature,
            raw=resp.raw,
        )

    def complete(
        self,
        *,
        model_code: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        feature: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        extra_options: Optional[Dict[str, Any]] = None,
    ) -> str:
        messages: List[Message] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        return self.chat(
            model_code=model_code,
            messages=messages,
            feature=feature,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_options=extra_options,
        ).text

    @staticmethod
    def _validate_messages(messages: Sequence[Message]) -> None:
        if not messages:
            raise ValueError("messages must not be empty")

        allowed_roles = {"system", "user", "assistant", "tool"}
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise TypeError(f"messages[{idx}] must be dict")
            role = msg.get("role")
            content = msg.get("content")
            if role not in allowed_roles:
                raise ValueError(
                    f"messages[{idx}].role must be one of {sorted(allowed_roles)}, got {role!r}"
                )
            if not isinstance(content, str):
                raise TypeError(f"messages[{idx}].content must be str")
