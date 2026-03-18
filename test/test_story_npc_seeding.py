from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = ROOT / "Code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from trpg_runtime import MinimalTRPGEngine


def test_det_story_seeds_opening_npcs() -> None:
    engine = MinimalTRPGEngine.from_prompt_files(
        rule_code="DET",
        story_code="THE_FIRSTMURDER",
        player_name="测试玩家",
    )
    try:
        assert "格雷森探长" in engine.state.scene.visible_npcs
        assert "塞拉斯·皮尔斯" in engine.state.npcs
        assert "伊莎贝拉·布莱克伍德" in engine.state.npcs
    finally:
        engine.shutdown(wait=False)


def test_nihon_story_seeds_background_npcs() -> None:
    engine = MinimalTRPGEngine.from_prompt_files(
        rule_code="NIHON",
        story_code="DAZHIZHAN",
        player_name="测试玩家",
    )
    try:
        assert "白坂忠藏" in engine.state.npcs
        assert "圆寂" in engine.state.npcs
        assert engine.state.npcs["白坂忠藏"].task_summary
    finally:
        engine.shutdown(wait=False)
