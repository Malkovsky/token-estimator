"""Pure token-accounting primitives shared by repository and upload flows."""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence


Count = Callable[[str], int]


@dataclass(frozen=True)
class TextSource:
    source: str
    content: str


@dataclass(frozen=True)
class SkillUsage:
    name: str
    metadata: int
    body: int
    optional: int


@dataclass(frozen=True)
class OptionalFileUsage:
    source: str
    tokens: int


@dataclass(frozen=True)
class SkillDetail:
    source: str
    declared_name: str
    usage: SkillUsage
    optional_files: tuple[OptionalFileUsage, ...]


@dataclass(frozen=True)
class McpUsage:
    name: str
    description: int
    schema: int
    definition: int


@dataclass(frozen=True)
class ContextUsage:
    source: str
    characters: int
    tokens: int


def heuristic_count(text: str) -> int:
    if not text:
        return 0
    return max(math.ceil(len(text) / 4), math.ceil(len(text.split()) * 1.3))


def make_counter(method: str, encoding: str) -> tuple[Count, str]:
    if method in ("auto", "tiktoken"):
        try:
            import tiktoken

            tokenizer = tiktoken.get_encoding(encoding)
            return lambda text: len(tokenizer.encode_ordinary(text)), f"tiktoken:{encoding}"
        except (ImportError, ValueError) as error:
            if method == "tiktoken":
                raise RuntimeError(
                    f"cannot use tiktoken encoding {encoding!r}: {error}"
                ) from error
    return heuristic_count, "heuristic:max(chars/4,words*1.3)"


def normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise ValueError(f"invalid relative path: {value!r}")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"invalid relative path: {value!r}")
    return PurePosixPath(*parts).as_posix()


def split_frontmatter(text: str, source: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{source}: missing YAML frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[: index + 1]), "".join(lines[index + 1 :])
    raise ValueError(f"{source}: unterminated YAML frontmatter")


def frontmatter_identity(frontmatter: str, source: str) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in frontmatter.splitlines()[1:-1]:
        key, separator, raw_value = line.partition(":")
        if separator and key in ("name", "description"):
            value = raw_value.strip()
            if value[:1] in ('"', "'"):
                try:
                    value = ast.literal_eval(value)
                except (SyntaxError, ValueError):
                    value = value[1:-1]
            values[key] = value
    missing = [key for key in ("name", "description") if not values.get(key)]
    if missing:
        raise ValueError(f"{source}: missing frontmatter field(s): {', '.join(missing)}")
    return values["name"], values["description"]


def _is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    if root == PurePosixPath("."):
        return True
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _skill_optional_sources(
    root: PurePosixPath,
    all_roots: Sequence[PurePosixPath],
    sources: Mapping[PurePosixPath, str],
) -> list[tuple[PurePosixPath, str]]:
    suffixes = {".json", ".md", ".rst", ".toml", ".txt", ".yaml", ".yml"}
    selected: list[tuple[PurePosixPath, str]] = []
    for path, content in sources.items():
        if path.name == "SKILL.md" or not _is_within(path, root):
            continue
        if any(other != root and _is_within(path, other) for other in all_roots):
            continue
        relative = path if root == PurePosixPath(".") else path.relative_to(root)
        if relative.suffix.lower() not in suffixes:
            continue
        if {".git", "agents", "assets", "scripts"}.intersection(relative.parts):
            continue
        selected.append((relative, content))
    return sorted(selected, key=lambda item: item[0].as_posix())


def measure_skill_bundle(files: Iterable[TextSource], count: Count) -> list[SkillDetail]:
    sources: dict[PurePosixPath, str] = {}
    for item in files:
        path = PurePosixPath(normalize_relative_path(item.source))
        if path in sources:
            raise ValueError(f"duplicate source path: {path.as_posix()}")
        sources[path] = item.content
    skill_files = sorted(
        (path for path in sources if path.name == "SKILL.md"),
        key=lambda path: path.as_posix(),
    )
    if not skill_files:
        raise ValueError("no SKILL.md files found")
    roots = [path.parent for path in skill_files]
    if len(set(roots)) != len(roots):
        raise ValueError("multiple SKILL.md files found in one skill directory")
    details: list[SkillDetail] = []
    for skill_file, root in zip(skill_files, roots):
        frontmatter, body = split_frontmatter(sources[skill_file], skill_file.as_posix())
        declared_name, description = frontmatter_identity(frontmatter, skill_file.as_posix())
        directory_name = root.name if root != PurePosixPath(".") else declared_name
        optional_files = tuple(
            OptionalFileUsage(source=path.as_posix(), tokens=count(content))
            for path, content in _skill_optional_sources(root, roots, sources)
        )
        details.append(
            SkillDetail(
                source=root.as_posix() if root != PurePosixPath(".") else skill_file.name,
                declared_name=declared_name,
                usage=SkillUsage(
                    name=directory_name,
                    metadata=count(declared_name) + count(description),
                    body=count(body),
                    optional=sum(item.tokens for item in optional_files),
                ),
                optional_files=optional_files,
            )
        )
    return details


def find_tool_lists(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value if all(isinstance(item, dict) for item in value) else []
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("tools"), list):
        return [item for item in value["tools"] if isinstance(item, dict)]
    if "name" in value or value.get("type") == "function":
        return [value]
    for nested in value.values():
        found = find_tool_lists(nested)
        if found:
            return found
    return []


def normalize_tool(tool: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        tool = tool["function"]
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("MCP tool is missing a non-empty name")
    description = tool.get("description", "")
    if not isinstance(description, str):
        raise ValueError(f"MCP tool {name!r} has a non-string description")
    schema = tool.get("inputSchema", tool.get("input_schema", tool.get("parameters", {})))
    if not isinstance(schema, dict):
        raise ValueError(f"MCP tool {name!r} has a non-object input schema")
    return name, description, schema


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def measure_mcp_documents(
    documents: Iterable[tuple[str, Any]], count: Count
) -> list[McpUsage]:
    usages: list[McpUsage] = []
    for source, document in documents:
        tools = find_tool_lists(document)
        if not tools:
            raise ValueError(f"{source}: no MCP tool definitions found")
        for raw_tool in tools:
            name, description, schema = normalize_tool(raw_tool)
            definition = {"name": name, "description": description, "inputSchema": schema}
            usages.append(
                McpUsage(
                    name=name,
                    description=count(description),
                    schema=count(compact_json(schema)),
                    definition=count(compact_json(definition)),
                )
            )
    return usages


def measure_context_sources(
    sources: Iterable[TextSource], count: Count
) -> list[ContextUsage]:
    return [
        ContextUsage(
            source=item.source,
            characters=len(item.content),
            tokens=count(item.content),
        )
        for item in sources
    ]
