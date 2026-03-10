"""
Text model helpers.

Step 1:
- Read the selected text model code from user preferences.
- Load the text model registry.
- Return the selected model's full configuration.
"""

from __future__ import annotations

from pathlib import Path

import data.data_Path as data_path
from tools.function_Preference import PreferenceManager
from user_info.option_TextModel import ModelConfig, ModelRegistry


CODE_ROOT = Path(__file__).resolve().parents[1]


def _resolve_code_path(path_str: str) -> Path:
    return CODE_ROOT / Path(path_str)


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

    registry_file = _resolve_code_path(data_path.PATH_DATA_LLM)
    if registry_path is not None:
        registry_file = Path(registry_path)

    registry = ModelRegistry.load(registry_file)
    return registry.get_by_code(model_code)
