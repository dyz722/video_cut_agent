"""Persist reusable editing lessons as learned skills.

The agent should store only compact, reusable preferences here. Raw transcripts,
private customer data, secrets, and one-off file names do not belong in skills.
"""

from datetime import datetime
import re
from pathlib import Path

from . import config


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text.strip().lower())
    slug = slug.strip("-")
    return slug[:80] or "general"


def _clean_text(text: str | None, limit: int = 3000) -> str:
    if not text:
        return ""
    cleaned = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned[:limit]


def _format_tags(tags) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        return tags
    return ", ".join(str(t).strip() for t in tags if str(t).strip())


def _initial_skill(scenario: str, skill_name: str) -> str:
    return f"""---
name: {skill_name}
description: User-approved editing lessons learned for {scenario}.
---

# Learned Editing Experience: {scenario}

This skill is updated by `record_experience` after a user confirms a result,
preference, or correction is useful. Load it together with the matching base
editing skill when working on a similar scenario.

## How To Use

- Prefer recent, concrete lessons that came from accepted outputs.
- Treat these notes as user preferences, not universal editing rules.
- Do not copy private file names, customer data, raw transcripts, or secrets
  into future outputs.

## Experience Log
"""


def record_experience(
    scenario: str,
    lesson: str,
    user_feedback: str = "",
    artifacts: str = "",
    tags=None,
) -> str:
    """Append a reusable editing lesson to a learned skill."""
    scenario = _clean_text(scenario, 120)
    lesson = _clean_text(lesson)
    if not scenario:
        return "Error: scenario is required."
    if not lesson:
        return "Error: lesson is required."

    slug = _slugify(scenario)
    skill_name = f"learned-{slug}"
    skill_dir = Path(config.LEARNED_SKILLS_DIR) / skill_name
    skill_file = skill_dir / "SKILL.md"
    skill_dir.mkdir(parents=True, exist_ok=True)

    if not skill_file.exists():
        skill_file.write_text(_initial_skill(scenario, skill_name), encoding="utf-8")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    tag_text = _format_tags(tags)
    entry = [
        "",
        f"### {timestamp}",
        "",
        f"- Scenario: {scenario}",
    ]
    if tag_text:
        entry.append(f"- Tags: {tag_text}")
    feedback = _clean_text(user_feedback, 1200)
    if feedback:
        entry.append(f"- User feedback: {feedback}")
    entry.append(f"- Lesson: {lesson}")
    artifact_text = _clean_text(artifacts, 1200)
    if artifact_text:
        entry.append(f"- Useful artifacts: {artifact_text}")
    entry.append("")

    with skill_file.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry))

    return (f"Recorded editing experience in skill '{skill_name}' "
            f"({skill_file}). Load it with load_skill(\"{skill_name}\").")
