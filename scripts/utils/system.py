import os, yaml
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.shell import SHELL
from utils.skill import SkillRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

load_dotenv(PROJECT_ROOT / ".env", override=True)  # 从项目根目录加载环境变量。


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path is not None else CONFIG_PATH
    if not config_path.exists():
        print(f"Warning: Config file '{config_path}' not found. Using default config.")
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


workspace = Path(load_config().get("paths", {}).get("workspace", ".")).expanduser()
WORKDIR = (workspace if workspace.is_absolute() else PROJECT_ROOT / workspace).resolve()


MESSAGES_DIR = WORKDIR / ".inpluscode" / ".sessions"  # WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".inpluscode" / ".task_outputs" / "tool-results"  # WORKDIR / ".task_outputs" / "tool-results"

MEMORY_DIR = WORKDIR / ".inpluscode" / ".memory"  # WORKDIR / ".memory"


MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

MEMORY_DIR_REL = ".inpluscode/.memory"

MODEL = os.getenv("MODEL_ID")

client = Anthropic(
    base_url=os.getenv("ANTHROPIC_BASE_URL"),
    auth_token=os.getenv("ANTHROPIC_AUTH_TOKEN"),
)
skill_registry = SkillRegistry(PROJECT_ROOT)
skill_registry.scan_skills()


# SYSTEM = (
#     f"You are a coding agent at {WORKDIR}. "
#     "For complex sub-problems, use the task tool to spawn a subagent."
# )

# SUB_SYSTEM = (
#     f"You are a coding agent at {WORKDIR}. "
#     "Complete the task you were given, then return a concise summary. "
#     "Do not delegate further."
# )


# deprecated: since 2026-07-17
# SYSTEM_legacy = (
#     f"You are a coding agent at {WORKDIR}. "
#     f"The bash tool executes commands with {SHELL};"
#     "For multi-step task, use todo_write to plan your steps. "
#     "Update todo status as you work;"
#     "For complex sub-problems, use the task tool to spawn a subagent."
# )

_catalog = skill_registry.list_skills()


# s07: Build SYSTEM prompt with skill catalog injected at startup.
SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    f"The bash tool executes commands with {SHELL}."
    "For multi-step task, use todo_write to plan your steps. "
    "Update todo status as you work. "
    # "For complex sub-problems, use the task tool to spawn a subagent. "
    "For separate task, use the task tool to delegate and spawn a subagent. "
    f"Skills available:\n{_catalog}\n"
    "Use load_skill to get full details when needed. "
    # s09 change: memory protocal
    "\nMemory: durable user preferences and project facts are stored as markdown files "
    f"under {MEMORY_DIR_REL}/. A <project_memory> index is provided at the start of the conversation."
    " Consult it first; use read_file to open a specific memory file only when its description is relevant."
    " Respect preferences recorded in memory."
)

# s06: subagent gets its own system prompt — no task, no recursion
SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    f"The bash tool executes commands with {SHELL};"
    "Use tools to complete the assigned task, then return a concise summary. "
    "Do not delegate further."
)
