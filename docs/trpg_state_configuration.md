# TRPG State Configuration Guide

This document explains how to configure the formal TRPG runtime state without editing Python code.
It is written for designers, scenario writers, and future AI maintainers.

## 1. State Architecture

The runtime state is split into four layers:

- `core`: shared runtime truth for all TRPGs
- `rule`: rule-family-specific runtime state
- `scenario`: story-specific state and branching data
- `agent_runtime`: hidden working state for Director, Narrator, Dicer, and NPC Manager

In code, these layers are defined in:

- `Code/trpg_runtime/models.py`

## 2. What Each Layer Stores

### `core`

Use `core` for information that exists in almost every TRPG:

- session clock and turn metadata
- player card
- current scene
- NPC registry
- locations
- items
- clues
- rolling event window
- archived chapter summary
- important player choices

Typical fields:

- `core.meta`
- `core.player`
- `core.scene`
- `core.npcs`
- `core.locations`
- `core.items`
- `core.clues`

### `rule`

Use `rule` for mechanics that depend on the rule family.

Examples:

- `rule.vhs.faith_track`
- `rule.det.evidence_chain`
- `rule.rom.affection_stage`
- `rule.coc.sanity`

Do not put generic scene or NPC facts here.

### `scenario`

Use `scenario` for information that belongs to one story only.

Examples:

- story objectives
- triggers
- secrets
- world facts
- unresolved questions
- foreshadow queue
- ending candidates

This is where a specific story should define its hidden structure.

### `agent_runtime`

Use `agent_runtime` for hidden engine-side working memory.

Examples:

- committed `director` state
- `last_narration`
- `last_player_action_text`
- `npc_manager_notes`

This layer is normally not authored heavily by planners.

## 3. Recommended Ownership

Use this rule of thumb when deciding what to configure manually.

- Designers should mainly author `scenario`, selected parts of `core`, and rule seeds inside `rule`.
- The runtime should own `recent_events`, `event_records`, `player_choices`, `check_history`, and `last_narration`.
- `agent_runtime` should usually start small and be updated by the engine.

Good fields for manual authoring:

- `core.scene`
- `core.npcs`
- `core.locations`
- `core.items`
- `core.clues`
- `rule.*`
- `scenario.objectives`
- `scenario.triggers`
- `scenario.secrets`
- `scenario.world_facts`

Fields that are usually runtime-owned:

- `core.meta.turn_id`
- `core.meta.game_day`
- `core.meta.game_hour`
- `core.meta.game_minute`
- `core.recent_events`
- `core.event_records`
- `core.player_choices`
- `rule.check_history`
- `agent_runtime.last_narration`

## 4. Content Folder Layout

The runtime now reads prompts from the new layout:

```text
Story/
  VHS/
    Rule/
      VHS_PROMPT.txt
      state_overrides.json
      core_state.json
      rule_state.json
      scenario_state.json
      agent_runtime_state.json
    Story/
      THE_ANGEL/
        THE_ANGEL.txt
        state_overrides.json
        core_state.json
        rule_state.json
        scenario_state.json
        agent_runtime_state.json
```

Agent prompts are read from:

```text
Code/data/data_TextPrompt/
  data_Beginning.txt
  data_Dicer.txt
  data_Director.txt
  data_Narrator.txt
  data_NpcManager.txt
```

## 5. Supported State Config Files

The loader supports both merged files and per-layer files.

### Rule-level files

- `Story/<RULE>/Rule/state_overrides.json`
- `Story/<RULE>/Rule/core_state.json`
- `Story/<RULE>/Rule/rule_state.json`
- `Story/<RULE>/Rule/scenario_state.json`
- `Story/<RULE>/Rule/agent_runtime_state.json`

### Story-level files

- `Story/<RULE>/Story/<STORY>/state_overrides.json`
- `Story/<RULE>/Story/<STORY>/core_state.json`
- `Story/<RULE>/Story/<STORY>/rule_state.json`
- `Story/<RULE>/Story/<STORY>/scenario_state.json`
- `Story/<RULE>/Story/<STORY>/agent_runtime_state.json`

### Extra override path

The engine also accepts one extra JSON file path:

- `state_override_path=".../custom.json"`

This is useful for testing, temporary tuning, or local experiments.

## 6. Merge Order

Files are merged in this order:

1. rule-level merged file
2. rule-level per-layer files
3. story-level merged file
4. story-level per-layer files
5. explicit `state_override_path`

Later files win.

That means:

- story-specific config overrides rule defaults
- local test config overrides both

## 7. JSON Format

### Option A: One merged override file

Example: `THE_ANGEL.state_overrides.json`

```json
{
  "core": {
    "scene": {
      "location": "Abandoned Chapel",
      "interactive_objects": ["altar", "confessional door", "candle stand"],
      "hazards": ["midnight escalation", "echoing footsteps"]
    },
    "npcs": {
      "Lucas": {
        "name": "Lucas",
        "description": "A frightened caretaker hiding part of the truth.",
        "is_visible": true,
        "current_goal": "keep the player away from the cellar"
      }
    }
  },
  "rule": {
    "vhs": {
      "faith_track": 2,
      "fear_stage": "uneasy"
    }
  },
  "scenario": {
    "world_facts": [
      "The chapel bell reacts to blood.",
      "The angel statue is not inert."
    ],
    "unresolved_questions": [
      "Who opened the crypt?",
      "Why does Lucas lie about the bell?"
    ]
  }
}
```

### Option B: Per-layer files

Example: `THE_ANGEL.scenario_state.json`

```json
{
  "world_facts": [
    "The chapel bell reacts to blood."
  ],
  "foreshadow_queue": [
    "A distant bell rings with no visible source."
  ],
  "ending_candidates": [
    "seal_the_altar",
    "follow_the_angel"
  ]
}
```

The loader will automatically wrap this as:

```json
{
  "scenario": {
    "...": "..."
  }
}
```

## 8. Authoring Rules

When writing state files, follow these rules:

- Use stable identifiers when possible.
- Put public world facts in `scenario.world_facts`, not inside Narrator notes.
- Put hidden NPC truth in `core.npcs.<id>.secret_summary`.
- Put rule resources in `rule`, not in `core.scene`.
- Put branching conditions and hidden progress in `scenario`, not in `agent_runtime`.
- Keep `agent_runtime` small unless the engine explicitly needs a seed.

## 9. Practical Workflow

Recommended workflow for a new story:

1. Write `Story/<RULE>/Rule/<RULE>_PROMPT.txt`
2. Write `Story/<RULE>/Story/<STORY>/<STORY>.txt`
3. Add rule defaults in `Story/<RULE>/Rule/rule_state.json`
4. Add story structure in `Story/<RULE>/Story/<STORY>/scenario_state.json`
5. Seed initial scene and NPCs in `Story/<RULE>/Story/<STORY>/core_state.json`
6. Keep `agent_runtime` empty unless you need an initial Director tone or hidden notes

## 10. Planner Checklist

Use this section as the practical fill-in checklist for planners.

### A. `core_state.json`

Purpose:

- define the playable starting world snapshot

Recommended file:

- `Story/<RULE>/Story/<STORY>/core_state.json`

Usually fill these:

- `player.name`
- `player.inventory`
- `player.known_clues`
- `player.status`
- `scene.location`
- `scene.description`
- `scene.visible_npcs`
- `scene.interactive_objects`
- `scene.hazards`
- `scene.exits`
- `npcs`
- `locations`
- `items`
- `clues`
- `global_flags`

Good minimum checklist:

- one starting scene
- all initially visible NPCs
- all initially interactable objects
- starting inventory
- starting clue visibility
- major location cards for places the player can soon reach

Usually do not fill these unless testing:

- `meta.turn_id`
- `meta.game_day`
- `meta.game_hour`
- `meta.game_minute`
- `recent_events`
- `event_records`
- `player_choices`
- `chapter_summary`

### B. `rule_state.json`

Purpose:

- define rule-family defaults and starting mechanics

Recommended file:

- `Story/<RULE>/Rule/rule_state.json`

Usually fill these:

- `family` if needed for explicit clarity
- `resource_pools`
- `status_effects`
- `tension_meters`
- `unlocked_mechanics`
- `disabled_mechanics`
- rule extension block for the chosen system

Per-rule examples:

- VHS:
  - `vhs.faith_track`
  - `vhs.fear_stage`
  - `vhs.monster_manifest_stage`
- DET:
  - `det.evidence_chain`
  - `det.suspect_matrix`
  - `det.testimony_conflicts`
- ROM:
  - `rom.affection_stage`
  - `rom.trust`
  - `rom.defense`
- COC:
  - `coc.sanity`
  - `coc.major_wound`
- NIHON:
  - `nihon.corruption`
  - `nihon.karma_tags`

Usually do not fill these unless making a resume/save file:

- `check_history`

### C. `scenario_state.json`

Purpose:

- define the story skeleton and hidden progression logic

Recommended file:

- `Story/<RULE>/Story/<STORY>/scenario_state.json`

This is the most important planner file.

Usually fill these:

- `title`
- `brief`
- `opening_scene`
- `objectives`
- `triggers`
- `secrets`
- `world_facts`
- `unresolved_questions`
- `active_branch_flags`
- `foreshadow_queue`
- `ending_candidates`

Good minimum checklist:

- 1 main objective
- 1 to 3 hidden truths
- 2 to 5 trigger seeds
- 2 to 6 world facts
- at least 1 fail direction or collapse direction
- at least 1 ending candidate

When writing `objectives`, try to include:

- objective id
- title
- type
- status
- related actors
- related locations
- related clues

When writing `triggers`, try to include:

- trigger id
- trigger type
- condition summary
- effect summary
- priority

When writing `secrets`, try to include:

- secret id
- title
- summary
- who knows it at start
- whether it starts revealed

### D. `agent_runtime_state.json`

Purpose:

- seed hidden agent-side working memory only when really necessary

Recommended file:

- `Story/<RULE>/Story/<STORY>/agent_runtime_state.json`

Usually leave this file empty or omit it.

Only fill it when you need:

- an initial Director tone
- starting hidden guidance
- a specific first-turn narration memory seed

Usually avoid filling:

- `last_narration`
- `last_player_action_text`
- `npc_manager_notes`
- `dicer_notes`

### E. Quick Assignment Rule

If a planner asks "which file should this go into?", use this shortcut:

- starting scene, NPCs, locations, items, clues: `core_state.json`
- rule resources and meters: `rule_state.json`
- hidden truth, branch logic, endings, triggers: `scenario_state.json`
- temporary agent hints only: `agent_runtime_state.json`

### F. Save File vs Design File

Design files should look like authored setup data.

They should not normally contain:

- recent turn logs
- accumulated event history
- previous narration output
- old check history

Those belong to a future save/export pipeline, not to planner-authored seeds.

## 11. Runtime Notes

Current engine behavior:

- old-style state access like `state.scene` still works
- old delta paths like `scene.noise_level` are mapped to the new layered state automatically
- Narrator now has a streaming path via `engine.stream_turn(...)`

## 12. Current Limitation

The formal schema is ready, but story text is not yet auto-parsed into structured objectives, triggers, secrets, locations, and clues.

Right now:

- the loader reads raw rule and story text
- state JSON files are the recommended way to seed formal structure

That means planners should treat the JSON files as the authoritative structured configuration layer.
