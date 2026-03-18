from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = ROOT / "Code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from trpg_runtime import MinimalTRPGEngine


def test_story_core_state_seeds_initial_npcs_into_scene() -> None:
    engine = MinimalTRPGEngine.from_prompt_files(
        rule_code="ADV",
        story_code="THE_DUST",
        player_name="测试玩家",
    )

    try:
        assert "阿吉" in engine.state.npcs
        assert engine.state.npcs["阿吉"].is_visible is True
        assert "阿吉" in engine.state.scene.visible_npcs
        assert engine.state.npcs["阿吉"].current_task is not None
        assert engine.state.npcs["阿吉"].deception_mode == "evasive"
        assert engine.state.npcs["阿吉"].contact_channels[0].channel == "in_person"
    finally:
        engine.shutdown(wait=False)
