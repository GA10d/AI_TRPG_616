from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_repo() -> Path:
    root = Path(__file__).resolve().parents[1]
    code_dir = root / "Code"
    if str(code_dir) not in sys.path:
        sys.path.insert(0, str(code_dir))
    return root


def main() -> None:
    root = _bootstrap_repo()

    from trpg_runtime import MinimalTRPGEngine, PromptRepository

    repo = PromptRepository()
    print("Available rules:", repo.list_rule_codes())
    print("VHS stories:", repo.list_story_codes("VHS"))

    engine = MinimalTRPGEngine.from_prompt_files(
        rule_code="VHS",
        story_code="THE_ANGEL",
        player_name="调查员",
        visible_npcs=["卢卡斯"],
        interactive_objects=["祭坛", "忏悔室木门"],
        hazards=["午夜后威胁升级"],
    )

    print("Repo root:", root)
    print("Scenario:", engine.state.scenario_title)
    print("Initial scene:", engine.state.scene.location)
    print("Initial time:", engine.state.meta.game_day, engine.state.meta.game_hour, engine.state.meta.game_minute)
    print("State JSON preview length:", len(engine.export_state_json()))

    if os.getenv("RUN_LLM_SMOKE") == "1":
        print("\nOpening preview:\n")
        print(engine.generate_opening())


if __name__ == "__main__":
    main()
