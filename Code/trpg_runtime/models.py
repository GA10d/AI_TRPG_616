from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


JsonScalar = str | int | float | bool | None
DeltaValue = JsonScalar | list[str] | list[int] | list[float] | list[bool]


class DeltaOperation(BaseModel):
    op: Literal["set", "append", "inc"] = Field(description="Delta operation type.")
    path: str = Field(description="Dot-separated state path to mutate.")
    value: DeltaValue = Field(description="Value used by the delta operation.")


class ParsedPlayerAction(BaseModel):
    raw_text: str = Field(description="Original player input after light normalization.")
    intent: str = Field(description="Lightweight intent label inferred from the input.")
    target: str | None = Field(default=None, description="Primary target mentioned in the action, if any.")
    approach: str | None = Field(default=None, description="High-level approach such as stealthy, aggressive, or social.")
    tags: list[str] = Field(default_factory=list, description="Auxiliary action tags extracted from the text.")


class DicerValidity(BaseModel):
    is_valid: bool = Field(description="Whether the action is valid under current rules and fiction.")
    reason: str = Field(description="Short explanation for the validity judgment.")
    rule_refs: list[str] = Field(default_factory=list, description="Optional rule references supporting the judgment.")


class DicerResolution(BaseModel):
    outcome: str = Field(description="Resolved outcome label such as success, fail, or mixed.")
    reason: str = Field(description="Short explanation of why the outcome happened.")
    consequence: str = Field(description="Immediate consequence produced by the resolution.")


class DicerOutput(BaseModel):
    validity: DicerValidity = Field(default_factory=lambda: DicerValidity(is_valid=True, reason="", rule_refs=[]))
    resolution: DicerResolution = Field(
        default_factory=lambda: DicerResolution(outcome="mixed", reason="", consequence="")
    )
    state_delta: list[DeltaOperation] = Field(default_factory=list, description="State mutations caused by the resolution.")
    event_log_entries: list[str] = Field(default_factory=list, description="Objective event log entries for this turn.")


class NPCVisibleBeat(BaseModel):
    npc_name: str = Field(description="Visible NPC name.")
    label: str = Field(default="", description="Short NPC label combining role or vibe for monitor-style display.")
    action: str = Field(default="", description="Visible action taken by the NPC this turn.")
    dialogue: str = Field(default="", description="Public dialogue line from the NPC this turn.")
    emotion: str = Field(default="", description="Outward emotion currently shown by the NPC.")
    tone: str = Field(default="", description="Speech tone or delivery style such as cold, urgent, gentle, or evasive.")
    expression: str = Field(default="", description="Brief facial expression or body-language summary visible to the player.")
    inner_state_note: str = Field(
        default="",
        description="Private short note about the NPC's inner state for engine use, not direct player display.",
    )
    concealment_note: str = Field(
        default="",
        description="Private note about what the NPC is hiding, deflecting, or lying about this turn.",
    )


class NPCBackgroundBeat(BaseModel):
    npc_name: str = Field(description="Off-screen or background NPC name.")
    progress: str = Field(default="", description="Short summary of background progress for this NPC.")
    label: str = Field(default="", description="Short NPC label combining role or vibe for monitor-style display.")
    location: str = Field(default="", description="Current off-screen location or rough area of activity.")
    task: str = Field(default="", description="Task the NPC is currently handling off-screen.")
    eta_minutes: int | None = Field(default=None, description="Estimated minutes until the next meaningful update.")
    contact_plan: str = Field(default="", description="How the NPC may next surface or relay this update if at all.")
    state_change: str = Field(default="", description="Important off-screen state change such as injured, armed, allied, or suspicious.")


class NPCTimelineNote(BaseModel):
    npc_name: str = Field(description="NPC tied to the timeline note.")
    note: str = Field(default="", description="Short explanation of the timeline trigger, deadline, or callback.")
    due_in_minutes: int | None = Field(default=None, description="Minutes until the note should matter, if known.")
    trigger_reason: str = Field(default="", description="Why this note matters now, such as elapsed task time or scene entry.")


class NPCCoreStateUpdate(BaseModel):
    npc_name: str = Field(description="Core NPC name whose persistent portrayal card should be updated.")
    appearance: str = Field(default="", description="Stable outward appearance summary to persist when newly established or changed.")
    outfit: str = Field(default="", description="Current outfit or signature clothing summary to persist when newly established or changed.")
    demeanor: str = Field(default="", description="Stable outward bearing or manner to persist when newly established or changed.")
    current_mood: str = Field(default="", description="Current mood baseline to persist when newly established or changed.")
    physical_status: str = Field(default="", description="Current physical condition to persist when newly established or changed.")
    expression: str = Field(default="", description="Current visible expression or body language to persist when newly established or changed.")
    last_public_status: str = Field(default="", description="Current public-facing NPC status summary to persist when newly established or changed.")


class NPCManagerOutput(BaseModel):
    visible_npcs_output: list[NPCVisibleBeat] = Field(
        default_factory=list,
        description="Visible NPC reactions that Narrator may present this turn.",
    )
    background_updates: list[NPCBackgroundBeat] = Field(
        default_factory=list,
        description="Off-screen NPC progress not necessarily shown directly to the player.",
    )
    timeline_notes: list[NPCTimelineNote] = Field(
        default_factory=list,
        description="Timeline or callback notes that help future turns surface NPC task progress consistently.",
    )
    core_npc_updates: list[NPCCoreStateUpdate] = Field(
        default_factory=list,
        description="Structured persistent updates for core NPC portrayal cards such as appearance, mood, outfit, and physical status.",
    )
    active_visible_npcs: list[str] = Field(
        default_factory=list,
        description="Names of NPCs directly present in the scene this turn according to NPC Manager.",
    )
    active_background_npcs: list[str] = Field(
        default_factory=list,
        description="Names of NPCs considered active off-screen this turn according to NPC Manager.",
    )
    state_delta: list[DeltaOperation] = Field(default_factory=list, description="NPC-driven state mutations.")
    event_log_entries: list[str] = Field(default_factory=list, description="Objective NPC event log entries.")


class DirectorGuidance(BaseModel):
    pace_status: str = Field(default="stable", description="Director assessment of current pacing.")
    tone: str = Field(default="neutral", description="Recommended tone for the next narration window.")
    guidance: list[str] = Field(default_factory=list, description="Non-public guidance points for the next turn.")
    foreshadow: list[str] = Field(default_factory=list, description="Foreshadow elements to prepare for future turns.")
    endgame: bool = Field(default=False, description="Whether the scenario is moving into an ending phase.")


class DirectorOutput(BaseModel):
    guidance: DirectorGuidance = Field(default_factory=DirectorGuidance)
    triggered_events: list[str] = Field(default_factory=list, description="Director-level trigger activations.")
    state_delta: list[DeltaOperation] = Field(default_factory=list, description="Director state mutations.")
    event_log_entries: list[str] = Field(default_factory=list, description="Objective director event log entries.")


class RelationshipState(BaseModel):
    summary: str = Field(default="", description="Short human-readable relationship summary.")
    trust: int = Field(default=0, description="Relative trust score for this relationship edge.")
    tension: int = Field(default=0, description="Relative tension score for this relationship edge.")
    tags: list[str] = Field(default_factory=list, description="Relationship tags such as ally, suspicious, or indebted.")


class NPCContactChannel(BaseModel):
    channel: str = Field(description="Contact channel such as in_person, phone, radio, letter, or messenger.")
    availability: str = Field(default="", description="Whether the channel is available right now and under what limits.")
    notes: str = Field(default="", description="Optional short note about how this channel works for the NPC.")


class NPCCurrentTask(BaseModel):
    task_id: str = Field(default="", description="Stable identifier for the NPC's active task when available.")
    summary: str = Field(default="", description="Short summary of the task currently being executed.")
    status: str = Field(default="idle", description="Task status such as idle, active, blocked, complete, or interrupted.")
    location: str = Field(default="", description="Human-readable place where the task is being carried out.")
    eta_minutes: int | None = Field(default=None, description="Estimated minutes until the task should progress or complete.")
    progress_note: str = Field(default="", description="Latest short note about task progress.")
    next_trigger: str = Field(default="", description="Condition or timing note that should surface the next task update.")


class NpcState(BaseModel):
    name: str = Field(description="NPC display name.")
    description: str = Field(default="", description="Short NPC card summary.")
    identity: str = Field(default="", description="Role, job, or social identity such as caretaker, detective, or smuggler.")
    is_core_npc: bool = Field(default=False, description="Whether this NPC should be treated as a core scenario NPC for initialization and consistency tracking.")
    age: str = Field(default="", description="Approximate age or age band when relevant to characterization.")
    gender: str = Field(default="", description="Gender identity or presentation if the scenario defines it.")
    appearance: str = Field(default="", description="Short outward appearance summary used to keep portrayals consistent.")
    outfit: str = Field(default="", description="Current clothing or signature outfit summary used to keep portrayals consistent.")
    demeanor: str = Field(default="", description="Stable outward manner or bearing such as stern, gentle, aloof, or twitchy.")
    current_mood: str = Field(default="", description="Current mood or emotional baseline such as wary, calm, irritated, or grief-stricken.")
    physical_status: str = Field(default="", description="Current physical state such as healthy, tired, injured, soaked, dusty, or bloodied.")
    expression: str = Field(default="", description="Current visible expression or body-language note such as frowning, avoiding eye contact, or standing rigidly.")
    attitude: str = Field(default="neutral", description="High-level attitude shown to the player.")
    disposition_to_player: str = Field(default="neutral", description="Current disposition toward the player.")
    faction: str = Field(default="", description="Primary faction, family, organization, or alignment this NPC belongs to.")
    location: str = Field(default="", description="Human-readable current location.")
    location_id: str | None = Field(default=None, description="Canonical location identifier if known.")
    current_goal: str = Field(default="", description="Immediate goal driving this NPC right now.")
    long_term_goal: str = Field(default="", description="Longer objective or desire for the NPC.")
    hidden_goal: str = Field(default="", description="Private hidden goal that should not be exposed to the player directly.")
    personality_traits: list[str] = Field(default_factory=list, description="Stable personality traits such as cautious, proud, impulsive, or gentle.")
    values: list[str] = Field(default_factory=list, description="Core values or principles that constrain this NPC's choices.")
    stress_level: str = Field(default="steady", description="Current stress or pressure level for performance and dialogue tone.")
    mental_state: str = Field(default="", description="Current short psychological state such as guarded, panicked, grieving, or composed.")
    fears_or_triggers: list[str] = Field(default_factory=list, description="Known fears, vulnerabilities, or emotional trigger points.")
    capabilities: list[str] = Field(default_factory=list, description="Things the NPC can credibly do, access, or influence.")
    resource_notes: list[str] = Field(default_factory=list, description="Short notes about gear, allies, authority, money, or other practical resources.")
    info_access_level: str = Field(default="", description="How much sensitive or scenario-critical information the NPC plausibly knows.")
    task_summary: str = Field(default="", description="Background task summary used by NPC Manager.")
    active_task_id: str | None = Field(default=None, description="Active background task identifier, if any.")
    task_eta_minutes: int | None = Field(default=None, description="Estimated time until the active task progresses.")
    current_task: NPCCurrentTask | None = Field(default=None, description="Structured active task state for timeline tracking.")
    secret_summary: str = Field(default="", description="Private summary of the NPC's hidden truth or secret.")
    known_truths: list[str] = Field(default_factory=list, description="Secret or sensitive facts this NPC knows and may selectively reveal.")
    reveal_conditions: list[str] = Field(default_factory=list, description="Conditions under which the NPC may reveal hidden truth or shift stance.")
    deception_mode: str = Field(default="", description="Current deception stance such as honest, evasive, lying, half_truth, or silent.")
    last_public_status: str = Field(default="", description="Most recent public-facing state update for this NPC.")
    is_visible: bool = Field(default=False, description="Whether the NPC is currently visible in the active scene.")
    distance_to_player: str = Field(default="", description="Short relational or physical distance note such as nearby, in next room, or radio-only.")
    contact_channels: list[NPCContactChannel] = Field(default_factory=list, description="Ways the player or other actors can currently reach this NPC.")
    relationship_stage: str = Field(default="", description="Relationship stage used by romance or social-heavy rules.")
    known_clue_ids: list[str] = Field(default_factory=list, description="Clue identifiers known by this NPC.")
    relationship_edges: dict[str, RelationshipState] = Field(
        default_factory=dict,
        description="Relationships from this NPC to other actors keyed by actor id or name.",
    )
    other_npc_activity: list[str] = Field(default_factory=list, description="Short notes about current alliances, conflicts, or exchanges with other NPCs.")
    tags: list[str] = Field(default_factory=list, description="Free-form NPC tags for capability, faction, or archetype.")

    @field_validator("current_task", mode="before")
    @classmethod
    def _coerce_current_task(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            return {
                "summary": normalized,
                "status": "active",
                "progress_note": normalized,
            }
        return value


class DirectorState(BaseModel):
    pace_status: str = Field(default="stable", description="Last committed director pacing assessment.")
    current_tone: str = Field(default="neutral", description="Last committed narrative tone guidance.")
    pending_guidance: list[str] = Field(
        default_factory=list,
        description="Queued hidden guidance points for the next narration turn.",
    )
    triggered_events: list[str] = Field(default_factory=list, description="Director trigger activations already committed.")
    endgame: bool = Field(default=False, description="Whether the scenario has entered an endgame state.")


class GameMeta(BaseModel):
    schema_version: str = Field(default="2.0", description="Schema version for migration and save compatibility.")
    session_id: str = Field(description="Stable runtime session identifier.")
    rule_code: str = Field(description="Selected rule family code such as VHS, DET, or COC.")
    story_code: str = Field(description="Selected scenario or story code.")
    save_revision: int = Field(default=0, description="Monotonic save revision counter.")
    turn_id: int = Field(default=0, description="Current committed turn index.")
    round_id: int = Field(default=0, description="Optional higher-level round index.")
    phase: str = Field(default="opening", description="Current engine phase label.")
    chapter_id: str = Field(default="opening", description="Current chapter identifier.")
    scene_id: str = Field(default="opening", description="Current scene identifier.")
    game_day: int = Field(default=1, description="In-game day counter.")
    game_hour: int = Field(default=20, description="In-game hour on a 24-hour clock.")
    game_minute: int = Field(default=0, description="In-game minute within the current hour.")
    elapsed_minutes: int = Field(default=0, description="Total in-game minutes elapsed since session start.")


class PlayerState(BaseModel):
    actor_id: str = Field(default="player", description="Canonical player actor identifier.")
    name: str = Field(description="Displayed player character name.")
    archetype: str = Field(default="", description="Character role or archetype label.")
    background: str = Field(default="", description="Short player background summary.")
    hp: int | None = Field(default=None, description="Hit points or equivalent health resource if the rules use one.")
    status: list[str] = Field(default_factory=list, description="Current player status effects.")
    inventory: list[str] = Field(default_factory=list, description="Player inventory in human-readable form.")
    known_clues: list[str] = Field(default_factory=list, description="Clue identifiers or labels the player knows.")
    discovered_location_ids: list[str] = Field(default_factory=list, description="Locations already discovered by the player.")
    short_term_goals: list[str] = Field(default_factory=list, description="Current short-term player goals.")
    long_term_goals: list[str] = Field(default_factory=list, description="Current long-term player goals.")
    relationship_notes: list[str] = Field(default_factory=list, description="Player-facing relationship notes and impressions.")
    preference_tags: list[str] = Field(default_factory=list, description="Player style tags relevant to narration or scenario flow.")


class SceneState(BaseModel):
    location_id: str | None = Field(default=None, description="Canonical current location identifier.")
    location: str = Field(description="Human-readable current location name.")
    description: str = Field(default="", description="Current visible scene description or reminder.")
    visible_npcs: list[str] = Field(default_factory=list, description="NPCs currently visible to the player.")
    interactive_objects: list[str] = Field(default_factory=list, description="Objects the player can currently interact with.")
    hazards: list[str] = Field(default_factory=list, description="Current scene hazards visible or relevant to play.")
    exits: list[str] = Field(default_factory=list, description="Known exits or route options from the scene.")
    scene_flags: list[str] = Field(default_factory=list, description="Scene-level flags such as locked, noisy, cursed, or searched.")
    tension_level: str = Field(default="steady", description="Current scene tension label.")
    noise_level: int = Field(default=0, description="Accumulated scene noise or visibility pressure.")
    active_clue_ids: list[str] = Field(default_factory=list, description="Clues strongly associated with this scene right now.")
    deadline_hints: list[str] = Field(default_factory=list, description="Player-facing pressure hints tied to time or escalation.")


class LocationState(BaseModel):
    location_id: str = Field(description="Canonical location identifier.")
    name: str = Field(description="Human-readable location name.")
    description: str = Field(default="", description="Persistent location card summary.")
    category: str = Field(default="scene", description="Location category such as room, district, shrine, or dungeon.")
    parent_location_id: str | None = Field(default=None, description="Parent location identifier if this is nested.")
    discovered: bool = Field(default=False, description="Whether this location has been discovered by the player.")
    currently_accessible: bool = Field(default=True, description="Whether the player can currently reach this location.")
    risk_level: str = Field(default="unknown", description="Current risk label for this location.")
    state_flags: list[str] = Field(default_factory=list, description="Persistent location flags such as locked or contaminated.")
    visible_features: list[str] = Field(default_factory=list, description="Persistent visible features in this location.")
    hidden_feature_ids: list[str] = Field(default_factory=list, description="Hidden feature identifiers still unrevealed here.")
    item_ids: list[str] = Field(default_factory=list, description="Item identifiers currently associated with this location.")
    clue_ids: list[str] = Field(default_factory=list, description="Clue identifiers currently associated with this location.")
    connected_location_ids: list[str] = Field(default_factory=list, description="Adjacent or connected location identifiers.")


class ItemState(BaseModel):
    item_id: str = Field(description="Canonical item identifier.")
    name: str = Field(description="Human-readable item name.")
    description: str = Field(default="", description="Persistent item card summary.")
    category: str = Field(default="general", description="Item category such as clue, weapon, key, or ritual tool.")
    owner_actor_id: str | None = Field(default=None, description="Actor currently holding the item, if any.")
    location_id: str | None = Field(default=None, description="Location currently holding the item, if any.")
    discovered: bool = Field(default=False, description="Whether the player knows this item exists.")
    consumable: bool = Field(default=False, description="Whether using the item can consume or remove it.")
    state_flags: list[str] = Field(default_factory=list, description="Persistent flags such as broken, blessed, or bloodied.")


class ClueState(BaseModel):
    clue_id: str = Field(description="Canonical clue identifier.")
    title: str = Field(description="Human-readable clue title.")
    summary: str = Field(default="", description="Short clue card summary.")
    clue_type: str = Field(default="general", description="Clue category such as testimony, evidence, omen, or rumor.")
    discovered: bool = Field(default=False, description="Whether this clue has been discovered in the world.")
    player_known: bool = Field(default=False, description="Whether the player currently knows this clue.")
    reliability: str = Field(default="unknown", description="Reliability tag such as solid, partial, doubtful, or misleading.")
    source_entity_id: str | None = Field(default=None, description="Actor, item, or source entity linked to the clue.")
    location_id: str | None = Field(default=None, description="Location most closely tied to the clue.")
    linked_clue_ids: list[str] = Field(default_factory=list, description="Related clue identifiers.")
    linked_actor_ids: list[str] = Field(default_factory=list, description="Actors tied to this clue.")
    contradiction_targets: list[str] = Field(default_factory=list, description="Other clues or testimonies contradicted by this clue.")


class PlayerChoiceRecord(BaseModel):
    turn_id: int = Field(description="Turn in which the player made the choice.")
    action_text: str = Field(description="Original player action text.")
    summary: str = Field(default="", description="Short normalized summary of the choice.")
    tags: list[str] = Field(default_factory=list, description="Choice tags used for analytics, branching, or recap.")


class EventRecord(BaseModel):
    turn_id: int = Field(description="Turn in which the event was committed.")
    source: str = Field(description="Subsystem or agent that produced the event.")
    summary: str = Field(description="Objective event summary.")
    tags: list[str] = Field(default_factory=list, description="Event tags for retrieval or filtering.")


class ConversationTurnRecord(BaseModel):
    turn_id: int = Field(description="Committed turn index for this player/narrator exchange.")
    player_text: str = Field(description="Original player input for the turn.")
    narrator_text: str = Field(description="Narrator output committed for the turn.")
    action_summary: str = Field(default="", description="Short normalized action summary for retrieval or recap.")
    outcome: str = Field(default="", description="High-level adjudication outcome associated with the turn.")


class CoreState(BaseModel):
    meta: GameMeta = Field(description="Shared session metadata and clock state.")
    player: PlayerState = Field(description="Core player state shared across all rule families.")
    scene: SceneState = Field(description="Current active scene snapshot.")
    npcs: dict[str, NpcState] = Field(default_factory=dict, description="NPC registry keyed by canonical id or display name.")
    locations: dict[str, LocationState] = Field(default_factory=dict, description="Persistent location registry.")
    items: dict[str, ItemState] = Field(default_factory=dict, description="Persistent item registry.")
    clues: dict[str, ClueState] = Field(default_factory=dict, description="Persistent clue and evidence registry.")
    recent_events: list[str] = Field(default_factory=list, description="Short rolling event window used for prompts.")
    event_records: list[EventRecord] = Field(default_factory=list, description="Structured event ledger for replay or audits.")
    chapter_summary: str = Field(default="", description="Compressed summary of older history outside the rolling window.")
    player_choices: list[PlayerChoiceRecord] = Field(default_factory=list, description="Important player choices worth persisting.")
    global_flags: list[str] = Field(default_factory=list, description="Shared world or session flags not tied to one subsystem.")


class RuleCheckRecord(BaseModel):
    turn_id: int = Field(description="Turn in which this rules check occurred.")
    action_summary: str = Field(default="", description="Short summary of the action being checked.")
    outcome: str = Field(default="", description="Outcome label for the check.")
    consequence: str = Field(default="", description="Immediate consequence from the check.")
    rule_refs: list[str] = Field(default_factory=list, description="Rules referenced when making the judgment.")


class COCRuleExtension(BaseModel):
    sanity: int | None = Field(default=None, description="Current sanity value for COC-like play.")
    sanity_breakpoints: list[str] = Field(default_factory=list, description="Tracked sanity thresholds or breakpoints.")
    temporary_insanity: bool = Field(default=False, description="Whether the player is currently in temporary insanity.")
    major_wound: bool = Field(default=False, description="Whether the player has suffered a major wound.")


class VHSRuleExtension(BaseModel):
    faith_track: int | None = Field(default=None, description="Faith-style resource for VHS-like horror play.")
    fear_stage: str = Field(default="", description="Current fear stage.")
    monster_manifest_stage: str = Field(default="", description="Current manifestation stage of the threat.")
    countdown_tags: list[str] = Field(default_factory=list, description="Countdown or escalation markers active in the scenario.")


class DETRuleExtension(BaseModel):
    suspect_matrix: list[str] = Field(default_factory=list, description="Serialized suspect relationship or suspicion matrix.")
    evidence_chain: list[str] = Field(default_factory=list, description="Serialized evidence chain for the case.")
    testimony_conflicts: list[str] = Field(default_factory=list, description="Known testimony contradictions.")
    case_confidence: int = Field(default=0, description="Current confidence score in the solved theory.")


class ROMRuleExtension(BaseModel):
    affection_stage: str = Field(default="", description="Current romance or bond stage.")
    trust: int = Field(default=0, description="Aggregate romance trust score.")
    defense: int = Field(default=0, description="Aggregate emotional defense or guardedness score.")
    route_lock: list[str] = Field(default_factory=list, description="Locked or unlocked route markers.")


class NIHONRuleExtension(BaseModel):
    karma_tags: list[str] = Field(default_factory=list, description="Karmic or moral burden markers.")
    sacrifice_threshold: int | None = Field(default=None, description="Scenario sacrifice threshold if the rules use one.")
    corruption: int = Field(default=0, description="Corruption or defilement level.")
    moral_debt: list[str] = Field(default_factory=list, description="Outstanding moral debts or unresolved vows.")


class ADVRuleExtension(BaseModel):
    exploration_flags: list[str] = Field(default_factory=list, description="Adventure-specific exploration flags.")
    navigation_progress: list[str] = Field(default_factory=list, description="Adventure-specific navigation progress markers.")


class GenericRuleExtension(BaseModel):
    notes: list[str] = Field(default_factory=list, description="Fallback rule-specific notes for unmodeled systems.")


class RuleState(BaseModel):
    family: str = Field(description="Selected rule family code.")
    resource_pools: dict[str, int | float] = Field(default_factory=dict, description="Rule-level numeric resources such as HP or SAN.")
    status_effects: list[str] = Field(default_factory=list, description="Rule-level status effects outside the core player card.")
    tension_meters: dict[str, int | float] = Field(default_factory=dict, description="Rule-level meters such as fear, suspicion, or romance heat.")
    unlocked_mechanics: list[str] = Field(default_factory=list, description="Mechanics currently unlocked for the session.")
    disabled_mechanics: list[str] = Field(default_factory=list, description="Mechanics currently disabled or unavailable.")
    check_history: list[RuleCheckRecord] = Field(default_factory=list, description="Structured log of past rule adjudications.")
    rule_flags: list[str] = Field(default_factory=list, description="Rule-family flags not covered by dedicated fields.")
    coc: COCRuleExtension | None = Field(default=None, description="COC-specific runtime state.")
    vhs: VHSRuleExtension | None = Field(default=None, description="VHS-specific runtime state.")
    det: DETRuleExtension | None = Field(default=None, description="DET-specific runtime state.")
    rom: ROMRuleExtension | None = Field(default=None, description="ROM-specific runtime state.")
    nihon: NIHONRuleExtension | None = Field(default=None, description="NIHON-specific runtime state.")
    adv: ADVRuleExtension | None = Field(default=None, description="ADV-specific runtime state.")
    generic: GenericRuleExtension | None = Field(default=None, description="Fallback runtime state for unsupported rule families.")


class ObjectiveState(BaseModel):
    objective_id: str = Field(description="Canonical objective identifier.")
    title: str = Field(description="Objective title.")
    summary: str = Field(default="", description="Short description of the objective and its stakes.")
    objective_type: str = Field(default="main", description="Objective type such as main, side, hidden, or fail condition.")
    status: str = Field(default="active", description="Objective status such as locked, active, completed, or failed.")
    related_actor_ids: list[str] = Field(default_factory=list, description="Actors tied to this objective.")
    related_clue_ids: list[str] = Field(default_factory=list, description="Clues tied to this objective.")
    related_location_ids: list[str] = Field(default_factory=list, description="Locations tied to this objective.")


class TriggerState(BaseModel):
    trigger_id: str = Field(description="Canonical trigger identifier.")
    trigger_type: str = Field(default="turn", description="Trigger type such as turn, time, clue_count, or scene_entry.")
    enabled: bool = Field(default=True, description="Whether this trigger is currently active.")
    consumed: bool = Field(default=False, description="Whether this trigger has already fired.")
    condition_summary: str = Field(default="", description="Human-readable trigger condition summary.")
    effect_summary: str = Field(default="", description="Human-readable effect summary for this trigger.")
    priority: int = Field(default=0, description="Priority used when multiple triggers are eligible.")


class SecretState(BaseModel):
    secret_id: str = Field(description="Canonical secret identifier.")
    title: str = Field(description="Secret title.")
    summary: str = Field(default="", description="Private secret summary.")
    revealed: bool = Field(default=False, description="Whether the secret has been revealed in play.")
    known_actor_ids: list[str] = Field(default_factory=list, description="Actors who currently know this secret.")


class ScenarioState(BaseModel):
    title: str = Field(description="Scenario title.")
    brief: str = Field(default="", description="Short scenario summary used for recap and prompt building.")
    opening_scene: str = Field(default="", description="Opening scene reference text extracted from the scenario.")
    current_arc: str = Field(default="opening", description="Current scenario arc or act.")
    current_stage: str = Field(default="opening", description="Current scenario stage within the arc.")
    objectives: dict[str, ObjectiveState] = Field(default_factory=dict, description="Scenario objectives keyed by objective id.")
    triggers: dict[str, TriggerState] = Field(default_factory=dict, description="Scenario triggers keyed by trigger id.")
    secrets: dict[str, SecretState] = Field(default_factory=dict, description="Scenario secrets keyed by secret id.")
    world_facts: list[str] = Field(default_factory=list, description="Persistent scenario truths and world facts.")
    unresolved_questions: list[str] = Field(default_factory=list, description="Outstanding player-facing or hidden questions.")
    active_branch_flags: list[str] = Field(default_factory=list, description="Scenario branch flags currently active.")
    foreshadow_queue: list[str] = Field(default_factory=list, description="Foreshadow elements queued for future turns.")
    ending_candidates: list[str] = Field(default_factory=list, description="Possible ending routes currently in play.")
    fail_state: str = Field(default="", description="Current fail state or collapse marker, if any.")


class RuleSeed(BaseModel):
    family: str = Field(
        default="",
        description="Rule family code for the parsed rule seed. May be omitted by the parser and filled from the selected rule.",
    )
    resource_pools: dict[str, int | float] = Field(
        default_factory=dict,
        description="Starting rule resources explicitly supported by the rule text.",
    )
    status_effects: list[str] = Field(default_factory=list, description="Starting rule statuses explicitly described.")
    tension_meters: dict[str, int | float] = Field(
        default_factory=dict,
        description="Starting tension or progression meters supported by the rule text.",
    )
    unlocked_mechanics: list[str] = Field(
        default_factory=list,
        description="Mechanics that should start unlocked under this rule family.",
    )
    disabled_mechanics: list[str] = Field(
        default_factory=list,
        description="Mechanics that should start disabled under this rule family.",
    )
    rule_flags: list[str] = Field(default_factory=list, description="Additional rule-family flags.")
    coc: COCRuleExtension | None = Field(default=None, description="Parsed COC-specific rule seed.")
    vhs: VHSRuleExtension | None = Field(default=None, description="Parsed VHS-specific rule seed.")
    det: DETRuleExtension | None = Field(default=None, description="Parsed DET-specific rule seed.")
    rom: ROMRuleExtension | None = Field(default=None, description="Parsed ROM-specific rule seed.")
    nihon: NIHONRuleExtension | None = Field(default=None, description="Parsed NIHON-specific rule seed.")
    adv: ADVRuleExtension | None = Field(default=None, description="Parsed ADV-specific rule seed.")
    generic: GenericRuleExtension | None = Field(default=None, description="Parsed generic rule seed.")


class ObjectiveSeed(BaseModel):
    objective_id: str = Field(default="", description="Suggested stable objective identifier.")
    title: str = Field(description="Objective title extracted from the story.")
    summary: str = Field(default="", description="Short summary of what this objective means.")
    objective_type: str = Field(default="main", description="Objective type such as main, side, hidden, or fail.")
    status: str = Field(default="active", description="Starting objective status.")
    related_actor_ids: list[str] = Field(default_factory=list, description="Actors tied to this objective.")
    related_clue_ids: list[str] = Field(default_factory=list, description="Clues tied to this objective.")
    related_location_ids: list[str] = Field(default_factory=list, description="Locations tied to this objective.")


class TriggerSeed(BaseModel):
    trigger_id: str = Field(default="", description="Suggested stable trigger identifier.")
    trigger_type: str = Field(default="turn", description="Trigger type such as turn, time, scene_entry, or clue.")
    enabled: bool = Field(default=True, description="Whether the trigger should start enabled.")
    consumed: bool = Field(default=False, description="Whether the trigger starts already consumed.")
    condition_summary: str = Field(description="Short human-readable trigger condition.")
    effect_summary: str = Field(description="Short human-readable trigger effect.")
    priority: int = Field(default=0, description="Relative trigger priority.")


class SecretSeed(BaseModel):
    secret_id: str = Field(default="", description="Suggested stable secret identifier.")
    title: str = Field(description="Secret title extracted from the story.")
    summary: str = Field(description="Short summary of the hidden truth.")
    revealed: bool = Field(default=False, description="Whether the secret should start revealed.")
    known_actor_ids: list[str] = Field(default_factory=list, description="Actors that know the secret at start.")


class ScenarioSeed(BaseModel):
    title: str = Field(default="", description="Scenario title if it should override the text-derived title.")
    brief: str = Field(default="", description="Short scenario summary extracted from the story.")
    opening_scene: str = Field(default="", description="Opening scene summary extracted from the story.")
    current_arc: str = Field(default="opening", description="Starting arc label.")
    current_stage: str = Field(default="opening", description="Starting stage label.")
    objectives: list[ObjectiveSeed] = Field(default_factory=list, description="Parsed scenario objectives.")
    triggers: list[TriggerSeed] = Field(default_factory=list, description="Parsed scenario triggers.")
    secrets: list[SecretSeed] = Field(default_factory=list, description="Parsed scenario secrets.")
    world_facts: list[str] = Field(default_factory=list, description="Stable world facts inferred from the story.")
    unresolved_questions: list[str] = Field(default_factory=list, description="Questions the story presents but does not answer immediately.")
    active_branch_flags: list[str] = Field(default_factory=list, description="Starting branch flags.")
    foreshadow_queue: list[str] = Field(default_factory=list, description="Foreshadow beats extracted from the story.")
    ending_candidates: list[str] = Field(default_factory=list, description="Possible ending routes implied by the story.")
    fail_state: str = Field(default="", description="Starting fail or collapse direction if clearly implied.")


class AgentRuntimeState(BaseModel):
    director: DirectorState = Field(default_factory=DirectorState, description="Last committed Director runtime state.")
    last_narration: str = Field(default="", description="Last committed narrator output.")
    last_player_action_text: str = Field(default="", description="Most recent committed player action text.")
    dialogue_window: list[ConversationTurnRecord] = Field(
        default_factory=list,
        description="Rolling player/narrator dialogue window used to avoid replaying the full transcript.",
    )
    narrator_memory: list[str] = Field(default_factory=list, description="Narrator-side short memory notes.")
    dicer_notes: list[str] = Field(default_factory=list, description="Dicer-side runtime notes not meant for the player.")
    npc_manager_notes: list[str] = Field(default_factory=list, description="NPC Manager runtime notes not meant for the player.")


class GameState(BaseModel):
    core: CoreState = Field(description="Shared state present for all TRPG sessions.")
    rule: RuleState = Field(description="Rule-family-specific runtime state.")
    scenario: ScenarioState = Field(description="Scenario-specific state and branching information.")
    agent_runtime: AgentRuntimeState = Field(description="Non-public runtime state used by the coordinating agents.")

    @property
    def meta(self) -> GameMeta:
        return self.core.meta

    @meta.setter
    def meta(self, value: GameMeta) -> None:
        self.core = self.core.model_copy(update={"meta": value})

    @property
    def player(self) -> PlayerState:
        return self.core.player

    @player.setter
    def player(self, value: PlayerState) -> None:
        self.core = self.core.model_copy(update={"player": value})

    @property
    def scene(self) -> SceneState:
        return self.core.scene

    @scene.setter
    def scene(self, value: SceneState) -> None:
        self.core = self.core.model_copy(update={"scene": value})

    @property
    def npcs(self) -> dict[str, NpcState]:
        return self.core.npcs

    @npcs.setter
    def npcs(self, value: dict[str, NpcState]) -> None:
        self.core = self.core.model_copy(update={"npcs": value})

    @property
    def director(self) -> DirectorState:
        return self.agent_runtime.director

    @director.setter
    def director(self, value: DirectorState) -> None:
        self.agent_runtime = self.agent_runtime.model_copy(update={"director": value})

    @property
    def scenario_title(self) -> str:
        return self.scenario.title

    @scenario_title.setter
    def scenario_title(self, value: str) -> None:
        self.scenario = self.scenario.model_copy(update={"title": value})

    @property
    def scenario_brief(self) -> str:
        return self.scenario.brief

    @scenario_brief.setter
    def scenario_brief(self, value: str) -> None:
        self.scenario = self.scenario.model_copy(update={"brief": value})

    @property
    def recent_events(self) -> list[str]:
        return self.core.recent_events

    @recent_events.setter
    def recent_events(self, value: list[str]) -> None:
        self.core = self.core.model_copy(update={"recent_events": value})

    @property
    def chapter_summary(self) -> str:
        return self.core.chapter_summary

    @chapter_summary.setter
    def chapter_summary(self, value: str) -> None:
        self.core = self.core.model_copy(update={"chapter_summary": value})


class TurnResult(BaseModel):
    action: ParsedPlayerAction = Field(description="Parsed player action used for the committed turn.")
    dicer_result: DicerOutput = Field(description="Committed Dicer output for this turn.")
    npc_result: NPCManagerOutput = Field(description="Committed NPC Manager output for this turn.")
    director_state_used: DirectorState = Field(description="Director state consumed by Narrator this turn.")
    narration: str = Field(description="Final narrator output committed for this turn.")
    state: GameState = Field(description="State snapshot after the turn commit.")
    next_director_result: DirectorOutput | None = Field(
        default=None,
        description="Synchronously produced next Director output when not running in background mode.",
    )
    pending_director_started: bool = Field(
        default=False,
        description="Whether a background Director job was started for the next turn.",
    )


class RuntimeLogEvent(BaseModel):
    event: Literal["runtime_log"] = Field(default="runtime_log", description="Structured runtime log event type.")
    phase: str = Field(description="High-level phase such as opening or turn.")
    stage: str = Field(description="Machine-friendly stage key within the phase.")
    message: str = Field(description="Human-readable log message.")
    payload: dict[str, object] = Field(
        default_factory=dict,
        description="Optional structured metadata for frontends or diagnostics.",
    )


class TurnDebugTrace(BaseModel):
    action: ParsedPlayerAction = Field(description="Parsed player action for the debug turn.")
    director_state_used: DirectorState = Field(description="Director state that Narrator consumed in this trace.")
    dicer_context: dict[str, object] = Field(description="LLM context passed to Dicer.")
    dicer_result: DicerOutput = Field(description="Dicer output produced in the trace.")
    state_before_turn: GameState = Field(description="State snapshot before any turn logic runs.")
    state_after_dicer: GameState = Field(description="State snapshot after Dicer delta application.")
    npc_context: dict[str, object] = Field(description="LLM context passed to NPC Manager.")
    npc_result: NPCManagerOutput = Field(description="NPC Manager output produced in the trace.")
    state_after_npc: GameState = Field(description="State snapshot after NPC delta application.")
    narrator_context: dict[str, object] = Field(description="LLM context passed to Narrator.")
    narration: str = Field(description="Narrator output produced in the trace.")
    state_after_turn: GameState = Field(description="State snapshot after the turn but before next Director commit.")
    next_director_context: dict[str, object] = Field(description="LLM context prepared for the next Director update.")
    next_director_result: DirectorOutput | None = Field(
        default=None,
        description="Director output computed for debugging when requested.",
    )
    state_after_next_director: GameState | None = Field(
        default=None,
        description="State snapshot after applying the next Director result in debug mode.",
    )


class TurnStreamEvent(BaseModel):
    event: Literal["agent_update", "narration_chunk", "turn_result"] = Field(
        description="Streaming event type emitted during a streamed turn."
    )
    agent_name: str = Field(default="", description="Agent name for agent_update events.")
    payload: dict[str, object] = Field(default_factory=dict, description="Agent payload for agent_update events.")
    delta: str = Field(default="", description="Narration text chunk for narration_chunk events.")
    result: TurnResult | None = Field(
        default=None,
        description="Final committed turn result emitted at the end of a streamed turn.",
    )


def build_rule_state(rule_code: str) -> RuleState:
    normalized = rule_code.upper()
    state = RuleState(family=normalized)
    if normalized == "COC":
        return state.model_copy(update={"coc": COCRuleExtension()})
    if normalized == "VHS":
        return state.model_copy(update={"vhs": VHSRuleExtension()})
    if normalized == "DET":
        return state.model_copy(update={"det": DETRuleExtension()})
    if normalized == "ROM":
        return state.model_copy(update={"rom": ROMRuleExtension()})
    if normalized == "NIHON":
        return state.model_copy(update={"nihon": NIHONRuleExtension()})
    if normalized == "ADV":
        return state.model_copy(update={"adv": ADVRuleExtension()})
    return state.model_copy(update={"generic": GenericRuleExtension()})
