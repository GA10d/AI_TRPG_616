from __future__ import annotations
from dataclasses import dataclass
from openai import OpenAI

@dataclass
class LLMClient:
    api_key: str
    base_url: str
    model: str

    def _client(self) -> OpenAI:
        return OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat(self, messages: list[dict], *, temperature: float=1.0, stream: bool=False, response_format=None):
        client = self._client()
        return client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            stream=stream,
            response_format=response_format
        )
