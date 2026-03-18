from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = ROOT / "Code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from trpg_runtime import parse_player_action


def test_parse_player_action_supports_expanded_chinese_keywords() -> None:
    action = parse_player_action("我想端详祭坛上的刻痕，然后翻看旁边的日记。", language_code="zh-CN")

    assert action.intent == "investigate"
    assert action.target is not None
    assert action.target in {"祭坛上的刻痕", "旁边的日记"}


def test_parse_player_action_supports_japanese_keywords() -> None:
    action = parse_player_action("机の上を調べる", language_code="ja")

    assert action.intent == "investigate"
    assert action.target is not None
    assert "机" in action.target


def test_parse_player_action_allows_local_custom_config() -> None:
    config_path = ROOT / "test" / "_action_parser_test.json"
    try:
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "default_language": "en",
                    "languages": {
                        "en": {
                            "intents": {
                                "ritual": ["chant", "invoke"],
                            },
                            "target_verbs": ["chant", "invoke"],
                            "tags": {
                                "quiet": ["softly"],
                            },
                        }
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        action = parse_player_action(
            "I softly chant the warding verse",
            config_path=str(config_path),
            language_code="en",
        )

        assert action.intent == "ritual"
        assert action.target is not None
        assert "warding verse" in action.target
        assert "quiet" in action.tags
    finally:
        config_path.unlink(missing_ok=True)
