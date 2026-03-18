from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = ROOT / "Code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from trpg_runtime import NPCManagerOutput


def test_npc_manager_output_accepts_extended_fields() -> None:
    payload = {
        "visible_npcs_output": [
            {
                "npc_name": "阿吉",
                "label": "阿吉（守陵人后裔）",
                "action": "抬手示意玩家停下",
                "dialogue": "先别走直线。",
                "emotion": "警惕",
                "tone": "压低声音",
                "expression": "盯着可疑地面",
                "inner_state_note": "担心玩家踩中暗板",
                "concealment_note": "仍隐瞒誓言相关真相",
            }
        ],
        "background_updates": [
            {
                "npc_name": "盗掘者老大",
                "label": "盗掘者老大（前压）",
                "progress": "在主殿继续推进。",
                "location": "主殿",
                "task": "补错误标记",
                "eta_minutes": 10,
                "contact_plan": "暂不会直接接触玩家",
                "state_change": "提高警戒",
            }
        ],
        "timeline_notes": [
            {
                "npc_name": "阿吉",
                "note": "如果玩家继续靠近祭坛，阿吉会给出更明确的警告。",
                "due_in_minutes": 5,
                "trigger_reason": "当前任务仍在进行中",
            }
        ],
        "active_visible_npcs": ["阿吉"],
        "active_background_npcs": ["盗掘者老大"],
        "state_delta": [],
        "event_log_entries": ["阿吉阻止玩家直踩可疑地面。"],
    }

    output = NPCManagerOutput.model_validate(payload)

    assert output.visible_npcs_output[0].tone == "压低声音"
    assert output.background_updates[0].eta_minutes == 10
    assert output.timeline_notes[0].npc_name == "阿吉"
    assert output.active_visible_npcs == ["阿吉"]
