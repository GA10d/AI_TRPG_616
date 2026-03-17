from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_ROOT = REPO_ROOT / "Prompt"
AGENT_PROMPT_ROOT = REPO_ROOT / "Code" / "data" / "data_TextPrompt"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _compact_text(text: str, max_chars: int) -> str:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        return stripped
    return "未命名剧本"


def _extract_opening_scene(text: str, max_chars: int = 480) -> str:
    lines = []
    seen_body = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") and not seen_body:
            continue
        seen_body = True
        lines.append(line)
        if len(" ".join(lines)) >= max_chars:
            break
    return _compact_text("\n".join(lines), max_chars=max_chars)


@dataclass(frozen=True)
class ScenarioBundle:
    rule_code: str
    story_code: str
    rule_text: str
    story_text: str
    beginning_prompt: str
    dicer_prompt: str
    npc_manager_prompt: str
    director_prompt: str
    narrator_prompt: str
    title: str
    opening_scene: str
    story_summary: str

    def rule_excerpt(self, max_chars: int) -> str:
        return _compact_text(self.rule_text, max_chars=max_chars)

    def story_excerpt(self, max_chars: int) -> str:
        return _compact_text(self.story_text, max_chars=max_chars)


class PromptRepository:
    def __init__(
        self,
        prompt_root: Path = PROMPT_ROOT,
        agent_prompt_root: Path = AGENT_PROMPT_ROOT,
    ) -> None:
        self.prompt_root = prompt_root
        self.agent_prompt_root = agent_prompt_root

    def list_rule_codes(self) -> list[str]:
        result: list[str] = []
        for path in sorted((self.prompt_root / "Rule").glob("*_PROMPT.txt")):
            result.append(path.stem.replace("_PROMPT", "").upper())
        return result

    def list_story_codes(self, rule_code: str) -> list[str]:
        story_dir = self.prompt_root / "Story" / rule_code.upper()
        if not story_dir.exists():
            return []
        return sorted(path.stem for path in story_dir.glob("*.txt"))

    def load_scenario(self, rule_code: str, story_code: str) -> ScenarioBundle:
        normalized_rule = rule_code.upper()

        rule_path = self.prompt_root / "Rule" / f"{normalized_rule}_PROMPT.txt"
        story_path = self._resolve_story_path(normalized_rule, story_code)
        beginning_path = self.prompt_root / "Function" / "BEGINNING_PROMPT.txt"

        if not rule_path.exists():
            raise FileNotFoundError(rule_path)
        if not story_path.exists():
            raise FileNotFoundError(story_path)

        rule_text = _read_text(rule_path)
        story_text = _read_text(story_path)
        beginning_prompt = _read_text(beginning_path)

        dicer_prompt = _read_text(self.agent_prompt_root / "data_Dicer.txt")
        npc_manager_prompt = _read_text(self.agent_prompt_root / "data_NpcManager.txt")
        director_prompt = _read_text(self.agent_prompt_root / "data_Director.txt")
        narrator_prompt = _read_text(self.agent_prompt_root / "data_Narrator.txt")

        return ScenarioBundle(
            rule_code=normalized_rule,
            story_code=story_path.stem,
            rule_text=rule_text,
            story_text=story_text,
            beginning_prompt=beginning_prompt,
            dicer_prompt=dicer_prompt,
            npc_manager_prompt=npc_manager_prompt,
            director_prompt=director_prompt,
            narrator_prompt=narrator_prompt,
            title=_extract_title(story_text),
            opening_scene=_extract_opening_scene(story_text),
            story_summary=_compact_text(story_text, max_chars=900),
        )

    def _resolve_story_path(self, rule_code: str, story_code: str) -> Path:
        story_dir = self.prompt_root / "Story" / rule_code
        if not story_dir.exists():
            raise FileNotFoundError(story_dir)

        exact = story_dir / f"{story_code}.txt"
        if exact.exists():
            return exact

        normalized_target = story_code.casefold()
        for candidate in story_dir.glob("*.txt"):
            if candidate.stem.casefold() == normalized_target:
                return candidate

        raise FileNotFoundError(story_dir / f"{story_code}.txt")
