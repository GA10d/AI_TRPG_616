from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

DeltaValue: TypeAlias = str | int | float | bool | None | list[str]


class DeltaOperation(BaseModel):
    op: Literal["set", "append", "inc"]
    path: str
    value: DeltaValue


class ParsedPlayerAction(BaseModel):
    raw_text: str
    intent: str = "general"
    target: str | None = None
    approach: str | None = None
    tags: list[str] = Field(default_factory=list)


class DicerValidity(BaseModel):
    is_legal: bool
    has_metagame_risk: bool = False
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class DicerResolution(BaseModel):
    success_chance: Literal["high", "medium", "low", "nearly_impossible"]
    outcome: Literal["success", "partial_success", "failure"]
    reason: str
    consequences: list[str] = Field(default_factory=list)


class DicerOutput(BaseModel):
    validity: DicerValidity
    resolution: DicerResolution
    visible_effects: list[str] = Field(default_factory=list)
    state_delta: list[DeltaOperation] = Field(default_factory=list)
    event_log_entries: list[str] = Field(default_factory=list)


class NpcState(BaseModel):
    name: str
    description: str = ""
    attitude: str = "neutral"
    location: str = ""
    current_goal: str = ""
    task_summary: str = ""
    last_public_status: str = ""
    is_visible: bool = False


class DirectorState(BaseModel):
    pace_status: str = "normal"
    current_tone: str = ""
    active_flags: list[str] = Field(default_factory=list)
    pending_guidance: list[str] = Field(default_factory=list)
    triggered_events: list[str] = Field(default_factory=list)
    endgame: bool = False


class GameMeta(BaseModel):
    session_id: str
    rule_code: str
    story_code: str
    turn_id: int = 0
    scene_id: str = "opening"
    game_day: int = 1
    game_hour: int = 20
    game_minute: int = 0


class PlayerState(BaseModel):
    name: str = "玩家"
    hp: int | None = None
    status: list[str] = Field(default_factory=list)
    inventory: list[str] = Field(default_factory=list)
    known_clues: list[str] = Field(default_factory=list)


class SceneState(BaseModel):
    location: str = "开场场景"
    description: str = ""
    visible_npcs: list[str] = Field(default_factory=list)
    interactive_objects: list[str] = Field(default_factory=list)
    hazards: list[str] = Field(default_factory=list)
    noise_level: int = 0


class GameState(BaseModel):
    meta: GameMeta
    player: PlayerState = Field(default_factory=PlayerState)
    scene: SceneState = Field(default_factory=SceneState)
    npcs: dict[str, NpcState] = Field(default_factory=dict)
    director: DirectorState = Field(default_factory=DirectorState)
    scenario_title: str = ""
    scenario_brief: str = ""
    recent_events: list[str] = Field(default_factory=list)
    chapter_summary: str = ""


class NPCVisibleBeat(BaseModel):
    npc_name: str
    dialogue: str = ""
    action: str = ""
    emotion: str = ""


class NPCBackgroundBeat(BaseModel):
    npc_name: str
    progress: str
    eta_hint: str = ""


class NPCManagerOutput(BaseModel):
    visible_npcs_output: list[NPCVisibleBeat] = Field(default_factory=list)
    background_updates: list[NPCBackgroundBeat] = Field(default_factory=list)
    scene_hints: list[str] = Field(default_factory=list)
    state_delta: list[DeltaOperation] = Field(default_factory=list)
    event_log_entries: list[str] = Field(default_factory=list)


class DirectorGuidance(BaseModel):
    pace_status: str = "normal"
    tone: str = ""
    guidance: list[str] = Field(default_factory=list)
    foreshadow: list[str] = Field(default_factory=list)
    endgame: bool = False
    ending_type: str = ""


class DirectorOutput(BaseModel):
    guidance: DirectorGuidance = Field(default_factory=DirectorGuidance)
    triggered_events: list[str] = Field(default_factory=list)
    state_delta: list[DeltaOperation] = Field(default_factory=list)
    event_log_entries: list[str] = Field(default_factory=list)


class TurnResult(BaseModel):
    action: ParsedPlayerAction
    dicer_result: DicerOutput
    npc_result: NPCManagerOutput
    director_state_used: DirectorState
    narration: str
    state: GameState
    next_director_result: DirectorOutput | None = None
    pending_director_started: bool = False


class TurnDebugTrace(BaseModel):
    action: ParsedPlayerAction
    director_state_used: DirectorState
    dicer_context: dict[str, Any]
    dicer_result: DicerOutput
    state_before_turn: GameState
    state_after_dicer: GameState
    npc_context: dict[str, Any]
    npc_result: NPCManagerOutput
    state_after_npc: GameState
    narrator_context: dict[str, Any]
    narration: str
    state_after_turn: GameState
    next_director_context: dict[str, Any]
    next_director_result: DirectorOutput | None = None
    state_after_next_director: GameState | None = None
