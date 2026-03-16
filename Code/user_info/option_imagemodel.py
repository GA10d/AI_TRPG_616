from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml


Dependence = Literal["Google", "OpenAI"]


@dataclass(frozen=True)
class T2IFeatureConfig:
    supported: bool
    model: Optional[str] = None
    url: Optional[str] = None

    @staticmethod
    def from_dict(d: Dict[str, Any], *, ctx: str) -> "T2IFeatureConfig":
        if not isinstance(d, dict):
            raise TypeError(f"{ctx} must be a dict, got {type(d).__name__}")

        supported = d.get("supported")
        if not isinstance(supported, bool):
            raise TypeError(f"{ctx}.supported must be bool, got {type(supported).__name__}")

        model = d.get("model")
        if model is not None and not isinstance(model, str):
            raise TypeError(f"{ctx}.model must be str|null, got {type(model).__name__}")

        url = d.get("url")
        if url is not None and not isinstance(url, str):
            raise TypeError(f"{ctx}.url must be str|null, got {type(url).__name__}")

        return T2IFeatureConfig(supported=supported, model=model, url=url)


@dataclass(frozen=True)
class T2IModelConfig:
    id: int
    name: str
    code: str
    dependence: Dependence
    url_requirements: bool
    base_url: Optional[str]
    base_model: str
    charge_url: str
    docs_url: str
    api_key_env: str
    features: Dict[str, T2IFeatureConfig]
    max_prompt_chars: Optional[int] = None

    @staticmethod
    def from_dict(d: Dict[str, Any], *, ctx: str) -> "T2IModelConfig":
        if not isinstance(d, dict):
            raise TypeError(f"{ctx} must be a dict, got {type(d).__name__}")

        def req_str(key: str) -> str:
            v = d.get(key)
            if not isinstance(v, str) or not v:
                raise TypeError(f"{ctx}.{key} must be non-empty str")
            return v

        def req_int(key: str) -> int:
            v = d.get(key)
            if not isinstance(v, int):
                raise TypeError(f"{ctx}.{key} must be int")
            return v

        def req_bool(key: str) -> bool:
            v = d.get(key)
            if not isinstance(v, bool):
                raise TypeError(f"{ctx}.{key} must be bool")
            return v

        model_id = req_int("id")
        name = req_str("name")
        code = req_str("code")

        dependence = d.get("dependence")
        if dependence not in ("Google", "OpenAI"):
            raise ValueError(f"{ctx}.dependence must be one of ['Google', 'OpenAI'], got {dependence!r}")

        url_requirements = req_bool("url_requirements")

        base_url = d.get("base_url")
        if base_url is not None and not isinstance(base_url, str):
            raise TypeError(f"{ctx}.base_url must be str|null")

        base_model = req_str("base_model")
        charge_url = req_str("charge_url")
        docs_url = req_str("docs_url")

        api_key_env = d.get("api_key_env", "GEMINI_API_KEY")
        if not isinstance(api_key_env, str) or not api_key_env:
            raise TypeError(f"{ctx}.api_key_env must be non-empty str")

        max_prompt_chars = d.get("max_prompt_chars")
        if max_prompt_chars is not None and (not isinstance(max_prompt_chars, int) or max_prompt_chars <= 0):
            raise TypeError(f"{ctx}.max_prompt_chars must be positive int|null")

        feats = d.get("features")
        if not isinstance(feats, dict):
            raise TypeError(f"{ctx}.features must be a dict")

        features: Dict[str, T2IFeatureConfig] = {}
        for feat_name, feat_dict in feats.items():
            features[str(feat_name)] = T2IFeatureConfig.from_dict(
                feat_dict, ctx=f"{ctx}.features.{feat_name}"
            )

        required_feature_keys = {"text_to_image"}
        missing = required_feature_keys - set(features.keys())
        if missing:
            raise ValueError(f"{ctx}.features missing keys: {sorted(missing)}")

        if url_requirements and not base_url:
            raise ValueError(f"{ctx}: url_requirements=true but base_url is null/empty")

        return T2IModelConfig(
            id=model_id,
            name=name,
            code=code,
            dependence=dependence,  # type: ignore[assignment]
            url_requirements=url_requirements,
            base_url=base_url,
            base_model=base_model,
            charge_url=charge_url,
            docs_url=docs_url,
            api_key_env=api_key_env,
            features=features,
            max_prompt_chars=max_prompt_chars,
        )

    def resolve_endpoint(self, *, feature: Optional[str] = None) -> tuple[Optional[str], str]:
        if feature is None:
            return (self.base_url, self.base_model)

        feat = self.features.get(feature)
        if feat is None:
            raise KeyError(f"Unknown feature {feature!r} for model {self.code!r}")
        if not feat.supported:
            raise ValueError(f"Feature {feature!r} not supported for model {self.code!r}")
        if not feat.model:
            raise ValueError(f"Feature {feature!r} supported but model is null for {self.code!r}")

        return (feat.url if feat.url else self.base_url, feat.model)

    def build_generate_content_payload(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        style: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Build a Gemini generateContent payload for text-to-image.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

        cleaned = prompt.strip()
        if self.max_prompt_chars is not None and len(cleaned) > self.max_prompt_chars:
            raise ValueError(
                f"prompt length={len(cleaned)} exceeds max_prompt_chars={self.max_prompt_chars} for model {self.code!r}"
            )

        extra_lines: List[str] = []
        if negative_prompt:
            extra_lines.append(f"Negative prompt: {negative_prompt.strip()}")
        if style:
            extra_lines.append(f"Style: {style.strip()}")
        if aspect_ratio:
            extra_lines.append(f"Aspect ratio: {aspect_ratio.strip()}")

        text = cleaned if not extra_lines else f"{cleaned}\n" + "\n".join(extra_lines)

        return {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }


@dataclass(frozen=True)
class T2IRegistryConfig:
    version: int
    models: List[T2IModelConfig]


class T2IModelRegistry:
    def __init__(self, cfg: T2IRegistryConfig):
        self._cfg = cfg
        self._by_id: Dict[int, T2IModelConfig] = {}
        self._by_code: Dict[str, T2IModelConfig] = {}

        for m in cfg.models:
            if m.id in self._by_id:
                raise ValueError(f"Duplicate model id: {m.id}")
            if m.code in self._by_code:
                raise ValueError(f"Duplicate model code: {m.code}")
            self._by_id[m.id] = m
            self._by_code[m.code] = m

        keys_sets = {tuple(sorted(m.features.keys())) for m in cfg.models}
        if len(keys_sets) != 1:
            raise ValueError(
                "Inconsistent feature keys across models: "
                + "; ".join(f"{m.code}:{sorted(m.features.keys())}" for m in cfg.models)
            )

    @staticmethod
    def load(path: str | Path) -> "T2IModelRegistry":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("YAML root must be a dict")

        version = data.get("version")
        if not isinstance(version, int):
            raise TypeError("version must be int")

        models_raw = data.get("models")
        if not isinstance(models_raw, list):
            raise TypeError("models must be a list")

        models = [
            T2IModelConfig.from_dict(m, ctx=f"models[{i}]")
            for i, m in enumerate(models_raw)
        ]

        return T2IModelRegistry(T2IRegistryConfig(version=version, models=models))

    def get_by_code(self, code: str) -> T2IModelConfig:
        try:
            return self._by_code[code]
        except KeyError:
            raise KeyError(f"Unknown model code: {code!r}")

    def get_by_id(self, model_id: int) -> T2IModelConfig:
        try:
            return self._by_id[model_id]
        except KeyError:
            raise KeyError(f"Unknown model id: {model_id}")

    def list_models(self) -> List[T2IModelConfig]:
        return list(self._cfg.models)

    def resolve(self, code: str, *, feature: Optional[str] = None) -> tuple[Optional[str], str]:
        return self.get_by_code(code).resolve_endpoint(feature=feature)


ImageFeatureConfig = T2IFeatureConfig
ImageModelConfig = T2IModelConfig
ImageModelRegistry = T2IModelRegistry


if __name__ == "__main__":
    data_file = Path(__file__).resolve().parents[1] / "data" / "data_ImageModel.yml"
    reg = T2IModelRegistry.load(data_file)
    base_url, model = reg.resolve("gemini")
    print("gemini base:", base_url, model)

    cfg = reg.get_by_code("gemini")
    payload = cfg.build_generate_content_payload(
        "White background, full-body anime male character standing pose.",
        style="clean cel-shading",
        aspect_ratio="2:3",
    )
    print(payload)
