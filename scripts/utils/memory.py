"""
s09_memory.py - Memory System

Persistent, cross-session knowledge for the coding agent.

Storage:
    .memory/
      MEMORY.md          ← index (one line per memory, ≤200 lines)
      feedback_tabs.md    ← individual memory files (Markdown + YAML frontmatter)
      user_profile.md
      project_facts.md

Flow in agent_loop:
    1. Load MEMORY.md index into SYSTEM prompt (cheap, always present)
    2. Select relevant memories by filename/description → inject content
    3. Run compression pipeline from s08
    4. After each turn ends → extract new memories from original messages
    5. Periodically consolidate (Dream)

Builds on s08 (context compact). Usage:

    python s09_memory/code.py
    Needs: pip install anthropic python-dotenv + ANTHROPIC_API_KEY in .env
"""

import re, json, time
from utils.system import MEMORY_DIR, MEMORY_INDEX, MODEL, client
from utils.tools import _extract_text

# ═══════════════════════════════════════════════════════════
#  NEW in s09: Memory System
# ═══════════════════════════════════════════════════════════

MEMORY_TYPES = ["user", "feedback", "project", "reference"]

MEMORY_INDEX_MAX_LINES = 200  # CC parity: first 200 lines
MEMORY_INDEX_MAX_BYTES = 25_000  # CC parity: or first 25KB

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, parts[2].strip()


def write_memory_file(name: str, mem_type: str, description: str, body: str):
    """Write a single memory file with YAML frontmatter."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-").replace("/", "-")
    filename = f"{slug}.md"
    filepath = MEMORY_DIR / filename
    filepath.write_text(
        f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    _rebuild_index()
    return filepath


def _rebuild_index():
    """Rebuild MEMORY.md index from all memory files."""
    lines = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name", f.stem)
        desc = meta.get("description", body.split("\n")[0][:80])
        lines.append(f"- [{name}]({f.name}) — {desc}")
    MEMORY_INDEX.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


# def read_memory_index() -> str:
#     """Read MEMORY.md index (injected into SYSTEM every turn)."""
#     if not MEMORY_INDEX.exists():
#         return ""
#     text = MEMORY_INDEX.read_text().strip()
#     return text if text else ""


def read_memory_index() -> str:
    """Read MEMORY.md index (first 200 lines / 25KB), matching Claude Code."""
    if not MEMORY_INDEX.exists():
        return ""
    lines = MEMORY_INDEX.read_text(encoding="utf-8").splitlines()[:MEMORY_INDEX_MAX_LINES]
    return "\n".join(lines)[:MEMORY_INDEX_MAX_BYTES].strip()


def read_memory_file(filename: str) -> str | None:
    """Read a single memory file's full content."""
    path = MEMORY_DIR / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def list_memory_files() -> list[dict]:
    """List all memory files with metadata."""
    result = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        result.append(
            {
                "filename": f.name,
                "name": meta.get("name", f.stem),
                "description": meta.get("description", ""),
                "type": meta.get("type", "user"),
                "body": body,
            }
        )
    return result


def select_relevant_memories(messages: list, max_items: int = 5) -> list[str]:
    """Select relevant memory filenames by matching recent conversation against
    memory names/descriptions. Uses a simple LLM call (or falls back to keyword
    matching on name+description)."""
    files = list_memory_files()
    if not files:
        return []

    # Collect recent user text for context
    recent_texts = []
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(str(getattr(b, "text", "")) for b in content if getattr(b, "type", None) == "text")
            if isinstance(content, str):
                recent_texts.append(content)
            if len(recent_texts) >= 3:
                break
    recent = " ".join(reversed(recent_texts))[:2000]

    if not recent.strip():
        return []

    # Build catalog of name + description for LLM to choose from
    catalog_lines = []
    for i, f in enumerate(files):
        catalog_lines.append(f"{i}: {f['name']} — {f['description']}")
    catalog = "\n".join(catalog_lines)

    prompt = (
        "Given the recent conversation and the memory catalog below, "
        "select the indices of memories that are clearly relevant. "
        "Return ONLY a JSON array of integers, e.g. [0, 3]. "
        "If none are relevant, return [].\n\n"
        f"Recent conversation:\n{recent}\n\n"
        f"Memory catalog:\n{catalog}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        text = _extract_text(response.content).strip()
        # Extract JSON array from response
        match = re.search(r"\[.*?\]", text, re.DOTALL)
        if match:
            indices = json.loads(match.group())
            selected = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(files):
                    selected.append(files[idx]["filename"])
                    if len(selected) >= max_items:
                        break
            return selected
    except Exception:
        pass

    # Fallback: keyword matching on name + description
    keywords = [w.lower() for w in recent.split() if len(w) > 3]
    selected = []
    for f in files:
        text = (f["name"] + " " + f["description"]).lower()
        if any(kw in text for kw in keywords):
            selected.append(f["filename"])
            if len(selected) >= max_items:
                break
    return selected


def load_memories(messages: list) -> str:
    """Load relevant memory content for injection into context."""
    selected_files = select_relevant_memories(messages)
    if not selected_files:
        return ""

    parts = ["<relevant_memories>"]
    for filename in selected_files:
        content = read_memory_file(filename)
        if content:
            parts.append(content)
    parts.append("</relevant_memories>")
    return "\n\n".join(parts)


def with_memory_context(messages: list, memory_index: str) -> list:
    """Request-only view: prepend the memory index as one synthetic
    project-context message. Canonical `messages` is never mutated, so transcript,
    compaction and extraction always see the raw dialogue. Front placement keeps a
    stable, cache-friendly prefix and never moves to the tail across turns."""

    if not memory_index:
        return messages
    preamble = {"role": "user", "content": f"<project_memory>\n{memory_index}\n</project_memory>"} # fmt: skip
    return [preamble, *messages]


def _extract_text(content: list) -> str:
    """Extract text from message content blocks."""
    if not isinstance(content, list):
        return str(content)
    return "\n".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")


def extract_memories(messages: list):
    """Extract new memories from recent dialogue. Runs after each turn."""
    # Collect recent conversation text
    dialogue_parts = []
    for msg in messages[-10:]:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(getattr(b, "text", "")) for b in content if getattr(b, "type", None) == "text")
        if isinstance(content, str) and content.strip():
            dialogue_parts.append(f"{role}: {content}")
    dialogue = "\n".join(dialogue_parts)

    if not dialogue.strip():
        return

    # Check existing memories to avoid duplicates
    existing = list_memory_files()
    existing_desc = "\n".join(f"- {m['name']}: {m['description']}" for m in existing) if existing else "(none)"

    prompt = (
        "Extract user preferences, constraints, or project facts from this dialogue.\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n"
        "- name: short kebab-case identifier (e.g. 'user-preference-tabs')\n"
        "- type: one of 'user' (user preference), 'feedback' (guidance), "
        "'project' (project fact), 'reference' (external pointer)\n"
        "- description: one-line summary for index lookup\n"
        "- body: full detail in markdown\n"
        "If nothing new or already covered by existing memories, return [].\n\n"
        f"Existing memories:\n{existing_desc}\n\n"
        f"Dialogue:\n{dialogue[:4000]}"
    )

    try:
        response = client.messages.create(model=MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=800)
        text = _extract_text(response.content).strip()
        # Extract JSON array from response
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return
        items = json.loads(match.group())
        if not items:
            return
        count = 0
        for mem in items:
            name = mem.get("name", f"memory_{int(time.time())}")
            mem_type = mem.get("type", "user")
            desc = mem.get("description", "")
            body = mem.get("body", "")
            if desc and body:
                write_memory_file(name, mem_type, desc, body)
                count += 1
        if count:
            print(f"\n\033[33m[Memory: extracted {count} new memories]\033[0m")
    except Exception:
        pass


CONSOLIDATE_THRESHOLD = 10


def consolidate_memories():
    """Merge duplicate/stale memories. Triggered when file count ≥ threshold."""
    files = list_memory_files()
    if len(files) < CONSOLIDATE_THRESHOLD:
        return

    catalog = "\n\n".join(f"## {f['filename']}\nname: {f['name']}\ndescription: {f['description']}\n{f['body']}" for f in files)

    prompt = (
        "Consolidate the following memory files. Rules:\n"
        "1. Merge duplicates into one\n"
        "2. Remove outdated/contradicted memories\n"
        "3. Keep the total under 30 memories\n"
        "4. Preserve important user preferences above all\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n\n"
        f"{catalog[:16000]}"
    )

    try:
        response = client.messages.create(model=MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=3000)
        text = _extract_text(response.content).strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return
        items = json.loads(match.group())

        # Remove old memory files (keep MEMORY.md)
        for f in MEMORY_DIR.glob("*.md"):
            if f.name != "MEMORY.md":
                f.unlink()

        for mem in items:
            name = mem.get("name", f"memory_{int(time.time())}")
            mem_type = mem.get("type", "user")
            desc = mem.get("description", "")
            body = mem.get("body", "")
            if desc and body:
                write_memory_file(name, mem_type, desc, body)

        print(f"\n\033[33m[Memory: consolidated {len(files)} → {len(items)} memories]\033[0m")
    except Exception:
        pass
