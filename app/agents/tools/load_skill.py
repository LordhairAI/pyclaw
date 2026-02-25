
import os
from pathlib import Path
from langchain.tools import tool
import logging
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger("uvicorn.error")

@tool
def load_skill(skill_name: str) -> str:
    """Load a specialized skill prompt.

    Auto-discovers skills from `<workspace>/skills/*/SKILL.md`.
    Returns the skill markdown content, or a helpful error with available skills.
    """
    workspace_root = os.getenv("WORKSPACE_ROOT")
    if workspace_root:
        skills_root = Path(workspace_root) / "skills"
    else:
        # Fallback to repo-relative path.
        skills_root = Path(__file__).resolve().parents[2] / "skills"

    if not skills_root.exists():
        return f"Skills directory not found: {skills_root}"

    skill_index: dict[str, Path] = {}
    for skill_file in skills_root.rglob("SKILL.md"):
        skill_key = skill_file.parent.name.strip().lower()
        if skill_key:
            skill_index[skill_key] = skill_file

    if not skill_index:
        return f"No skills found under: {skills_root}"

    normalized_name = skill_name.strip().lower()
    skill_path = skill_index.get(normalized_name)
    if skill_path is None:
        available = ", ".join(sorted(skill_index.keys()))
        return (
            f"Skill '{skill_name}' not found. "
            f"Available skills: {available}"
        )

    try:
        logger.info(f"skill: {skill_path}")
        return skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Failed to load skill '{skill_name}' from {skill_path}: {exc}"