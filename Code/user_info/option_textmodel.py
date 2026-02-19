# model_registry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Dict, List, Literal

import yaml


Dependence = Literal["OpenAI"]  # 你目前只有 OpenAI；以后可扩展 "Anthropic"/"Google"/"xAI" 等


@dataclass(frozen=True)
class FeatureConfig:
    supported: bool
    model: Optional[str] = None
    url: Optional[str] = None

    @staticmethod
    def from_dict(d: Dict[str, Any], *, ctx: str) -> "FeatureConfig":
        if not isinstance(d, dict):
            raise TypeError(f"{ctx} must be a dict, got {type(d).__name__}")

        supported = d.get("supported")
        if not isinstance(supported, bool):
            raise TypeError(f"{ctx}.supported must be bool, got {type(supported).__name__}")

        model = d.get("model", None)
        if model is not None and not isinstance(model, str):
            raise TypeError(f"{ctx}.model must be str|null, got {type(model).__name__}")

        url = d.get("url", None)
        if url is not None and not isinstance(url, str):
            raise TypeError(f"{ctx}.url must be str|null, got {type(url).__name__}")

        return FeatureConfig(supported=supported, model=model, url=url)


@dataclass(frozen=True)
class ModelConfig:
    id: int
    name: str
    code: str
    dependence: Dependence
    url_requirements: bool
    base_url: Optional[str]
    base_model: str
    charge_url: str
    docs_url: str
    features: Dict[str, FeatureConfig]

    @staticmethod
    def from_dict(d: Dict[str, Any], *, ctx: str) -> "ModelConfig":
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
        if dependence not in ("OpenAI",):
            raise ValueError(f"{ctx}.dependence must be one of ['OpenAI'], got {dependence!r}")

        url_requirements = req_bool("url_requirements")

        base_url = d.get("base_url", None)
        if base_url is not None and not isinstance(base_url, str):
            raise TypeError(f"{ctx}.base_url must be str|null")

        base_model = req_str("base_model")
        charge_url = req_str("charge_url")
        docs_url = req_str("docs_url")

        feats = d.get("features")
        if not isinstance(feats, dict):
            raise TypeError(f"{ctx}.features must be a dict")

        features: Dict[str, FeatureConfig] = {}
        for feat_name, feat_dict in feats.items():
            features[str(feat_name)] = FeatureConfig.from_dict(
                feat_dict, ctx=f"{ctx}.features.{feat_name}"
            )

        # 关键：结构一致性检查（你之前提到的“每个 part 一致吗”）
        required_feature_keys = {"mini_version", "deep_think", "json_output", "tool_calls"}
        missing = required_feature_keys - set(features.keys())
        if missing:
            raise ValueError(f"{ctx}.features missing keys: {sorted(missing)}")

        # URL requirement 约束：如果 url_requirements=true，则 base_url 必须存在
        if url_requirements and not base_url:
            raise ValueError(f"{ctx}: url_requirements=true but base_url is null/empty")

        return ModelConfig(
            id=model_id,
            name=name,
            code=code,
            dependence=dependence,  # type: ignore[assignment]
            url_requirements=url_requirements,
            base_url=base_url,
            base_model=base_model,
            charge_url=charge_url,
            docs_url=docs_url,
            features=features,
        )

    def resolve_endpoint(self, *, feature: Optional[str] = None) -> tuple[Optional[str], str]:
        """
        返回 (base_url, model_name)
        - feature=None -> 使用 base_model/base_url
        - feature=...  -> 使用 features[feature] 的 model/url（如果 supported）
        约定：
          - url 为 null 时，调用方用自己默认 base_url（比如 OpenAI 默认）即可
        """
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


@dataclass(frozen=True)
class RegistryConfig:
    version: int
    models: List[ModelConfig]


class ModelRegistry:
    def __init__(self, cfg: RegistryConfig):
        self._cfg = cfg
        self._by_id: Dict[int, ModelConfig] = {}
        self._by_code: Dict[str, ModelConfig] = {}

        for m in cfg.models:
            if m.id in self._by_id:
                raise ValueError(f"Duplicate model id: {m.id}")
            if m.code in self._by_code:
                raise ValueError(f"Duplicate model code: {m.code}")
            self._by_id[m.id] = m
            self._by_code[m.code] = m

        # 全局“features key 一致性检查”
        keys_sets = {tuple(sorted(m.features.keys())) for m in cfg.models}
        if len(keys_sets) != 1:
            raise ValueError(
                "Inconsistent feature keys across models: "
                + "; ".join(f"{m.code}:{sorted(m.features.keys())}" for m in cfg.models)
            )

    @staticmethod
    def load(path: str | Path) -> "ModelRegistry":
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
            ModelConfig.from_dict(m, ctx=f"models[{i}]")
            for i, m in enumerate(models_raw)
        ]
        return ModelRegistry(RegistryConfig(version=version, models=models))

    def get_by_code(self, code: str) -> ModelConfig:
        try:
            return self._by_code[code]
        except KeyError:
            raise KeyError(f"Unknown model code: {code!r}")

    def get_by_id(self, model_id: int) -> ModelConfig:
        try:
            return self._by_id[model_id]
        except KeyError:
            raise KeyError(f"Unknown model id: {model_id}")

    def list_models(self) -> List[ModelConfig]:
        return list(self._cfg.models)

    def resolve(self, code: str, *, feature: Optional[str] = None) -> tuple[Optional[str], str]:
        """
        快捷方法：按 code + feature 得到 (base_url, model_name)
        """
        return self.get_by_code(code).resolve_endpoint(feature=feature)


# ---------------------------
# 用法示例（可删）
# ---------------------------
if __name__ == "__main__":
    reg = ModelRegistry.load("models.yaml")

    # 1) 基础模型
    base_url, model = reg.resolve("deepseek")
    print("deepseek base:", base_url, model)

    # 2) deep_think
    base_url, model = reg.resolve("deepseek", feature="deep_think")
    print("deepseek deep_think:", base_url, model)

    # 3) ChatGPT mini_version（base_url 为 None，意味着使用 SDK 默认）
    base_url, model = reg.resolve("chatgpt", feature="mini_version")
    print("chatgpt mini:", base_url, model)
