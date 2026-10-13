"""Validate Skill metadata and the declared public distribution files."""

import re
import sys

import yaml

from distribution import NAME, RELEASE_FILES, ROOT, VERSION, checked_files


def validate(root=ROOT) -> None:
    files = checked_files(root, RELEASE_FILES)
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError("Missing YAML frontmatter")
    frontmatter = yaml.safe_load(match[1])
    if frontmatter.get("name") != NAME or not isinstance(frontmatter.get("description"), str):
        raise ValueError("Skill name or description is invalid")
    if not frontmatter["description"] or len(frontmatter["description"]) > 1024:
        raise ValueError("Description must contain 1 to 1024 characters")
    if frontmatter.get("metadata", {}).get("version") != VERSION:
        raise ValueError("Skill version does not match the release version")
    if "[" + "TODO:" in text:
        raise ValueError("Unfinished Skill scaffold")
    interface = yaml.safe_load((root / "agents" / "openai.yaml").read_text(encoding="utf-8"))["interface"]
    if not 25 <= len(interface["short_description"]) <= 64:
        raise ValueError("UI description must contain 25 to 64 characters")
    if "$" + NAME not in interface["default_prompt"]:
        raise ValueError("Default prompt must invoke this Skill")
    forbidden = (
        r"github_pat_[A-Za-z0-9_]{30,}", r"gh[pousr]_[A-Za-z0-9]{25,}",
        r"(?i)[a-z]:[\\/]Users[\\/][^\s]+", r"(?i)[a-z]:[\\/]YT(?:[\\/]|\b)",
        r"(?m)^# Netscape HTTP Cookie File",
    )
    for name, path in files:
        value = path.read_text(encoding="utf-8")
        if "\ufffd" in value:
            raise ValueError(f"Invalid or replaced text encoding in {name}")
        if any(re.search(pattern, value) for pattern in forbidden):
            raise ValueError(f"Private data or unfinished scaffold in {name}")
    print(f"Validated {NAME} {VERSION}: {len(files)} public files")


if __name__ == "__main__":
    try:
        validate()
    except (ValueError, KeyError, OSError, yaml.YAMLError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
