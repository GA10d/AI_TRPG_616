from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Protocol, Sequence, TypeVar

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from user_info.option_TextModel import ModelConfig


Message = Dict[str, str]
StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


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

    def parse_structured(
        self,
        request: ProviderRequest,
        output_schema: type[StructuredOutputT],
    ) -> StructuredOutputT:
        ...


class OpenAICompatibleTextProvider:
    """Provider adapter for OpenAI-compatible text generation APIs."""

    def generate(self, request: ProviderRequest) -> str:
        client = self._build_client(request.model_config, request.base_url)
        payload = self._build_chat_payload(request)
        completion = client.chat.completions.create(**payload)
        return completion.choices[0].message.content or ""

    def stream_generate(self, request: ProviderRequest) -> Iterator[str]:
        client = self._build_client(request.model_config, request.base_url)
        payload = self._build_chat_payload(request)
        payload["stream"] = True

        stream = client.chat.completions.create(**payload)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = getattr(chunk.choices[0].delta, "content", None)
            if delta:
                yield delta

    def parse_structured(
        self,
        request: ProviderRequest,
        output_schema: type[StructuredOutputT],
    ) -> StructuredOutputT:
        if request.model_config.dependence != "OpenAI" or request.base_url:
            raise NotImplementedError(
                "Structured outputs are currently implemented only for native "
                "OpenAI Responses API models."
            )

        client = self._build_client(request.model_config, request.base_url)
        payload: Dict[str, Any] = {
            "model": request.model_name,
            "input": list(request.messages),
            "text_format": output_schema,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_output_tokens"] = request.max_tokens
        if request.timeout is not None:
            payload["timeout"] = request.timeout
        if request.extra_options:
            payload.update(request.extra_options)

        response = client.responses.parse(**payload)
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("Structured output parsing returned None")
        return parsed

    def _build_client(self, model_config: ModelConfig, base_url: Optional[str]) -> OpenAI:
        api_key = self._resolve_api_key(model_config)
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        normalized_base_url = self._normalize_base_url(model_config, base_url)
        if normalized_base_url:
            client_kwargs["base_url"] = normalized_base_url
        return OpenAI(**client_kwargs)

    @staticmethod
    def _build_chat_payload(request: ProviderRequest) -> Dict[str, Any]:
        temperature = OpenAICompatibleTextProvider._normalize_temperature(request)
        payload: Dict[str, Any] = {
            "model": request.model_name,
            "messages": list(request.messages),
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.timeout is not None:
            payload["timeout"] = request.timeout
        if request.extra_options:
            payload.update(request.extra_options)
        return payload

    @staticmethod
    def _normalize_temperature(request: ProviderRequest) -> Optional[float]:
        if (
            request.model_config.dependence == "OpenAI"
            and request.base_url is None
            and "nano" in request.model_name.lower()
        ):
            return 1.0
        return request.temperature

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
        if model_config.code.lower() == "gemini" or model_config.dependence == "Google":
            env_candidates.insert(0, "GEMINI_API_KEY")
            env_candidates.insert(1, "GOOGLE_API_KEY")
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

    @staticmethod
    def _normalize_base_url(model_config: ModelConfig, base_url: Optional[str]) -> Optional[str]:
        if not base_url:
            return base_url

        normalized = base_url.rstrip("/")
        if model_config.dependence == "Google":
            suffix = "/v1beta/openai"
            if not normalized.endswith(suffix):
                normalized = f"{normalized}{suffix}"
            return f"{normalized}/"

        return base_url

