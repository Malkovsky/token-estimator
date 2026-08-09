"""Application service layer over the reusable estimator core."""

from __future__ import annotations

import fnmatch
import json
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any, Iterable

import tiktoken

from token_estimator import (
    TextSource,
    make_counter,
    measure_context_sources,
    measure_mcp_documents,
    measure_skill_bundle,
    normalize_relative_path,
)

from .config import Settings
from .schemas import (
    ContextRecord, ContextRequest, ContextResponse, McpRecord, McpRequest,
    McpResponse, MethodInfo, OptionalFileRecord, ScenarioBreakdown,
    ScenarioRequest, ScenarioResponse, SkillRecord, SkillsRequest, SkillsResponse,
)


@lru_cache(maxsize=None)
def available_encodings() -> tuple[str, ...]:
    return tuple(sorted(tiktoken.list_encoding_names()))


@lru_cache(maxsize=None)
def counter_for(encoding: str) -> Any:
    if encoding not in available_encodings():
        raise ValueError(f"unknown tiktoken encoding: {encoding}")
    count, _ = make_counter("tiktoken", encoding)
    return count


def method_info(encoding: str) -> MethodInfo:
    counter_for(encoding)
    return MethodInfo(encoding=encoding, version=tiktoken.__version__)


def selected_encoding(requested: str | None, settings: Settings) -> str:
    encoding = requested or settings.default_encoding
    method_info(encoding)
    return encoding


def validate_content(values: Iterable[str], settings: Settings) -> None:
    material = list(values)
    if len(material) > settings.max_items:
        raise ValueError(f"too many items; maximum is {settings.max_items}")
    size = sum(len(value.encode("utf-8")) for value in material)
    if size > settings.max_content_bytes:
        raise OverflowError(f"decoded content exceeds {settings.max_content_bytes} bytes")


def estimate_skills(request: SkillsRequest, settings: Settings) -> SkillsResponse:
    validate_content((item.content for item in request.files), settings)
    encoding = selected_encoding(request.encoding, settings)
    details = measure_skill_bundle(
        [TextSource(item.path, item.content) for item in request.files], counter_for(encoding)
    )
    records = [
        SkillRecord(
            id=detail.source, source=detail.source, name=detail.usage.name,
            declared_name=detail.declared_name, encoding=encoding,
            metadata=detail.usage.metadata, body=detail.usage.body,
            optional=detail.usage.optional,
            optional_files=[
                OptionalFileRecord(source=item.source, tokens=item.tokens)
                for item in detail.optional_files
            ],
        )
        for detail in details
    ]
    return SkillsResponse(
        method=method_info(encoding), records=records,
        totals={
            "metadata": sum(item.metadata for item in records),
            "body": sum(item.body for item in records),
            "optional": sum(item.optional for item in records),
        },
    )


def estimate_mcp(request: McpRequest, settings: Settings) -> McpResponse:
    validate_content((json.dumps(item.document, ensure_ascii=False) for item in request.documents), settings)
    encoding = selected_encoding(request.encoding, settings)
    records: list[McpRecord] = []
    for document_index, item in enumerate(request.documents):
        for tool_index, usage in enumerate(
            measure_mcp_documents([(item.source, item.document)], counter_for(encoding))
        ):
            records.append(McpRecord(
                id=f"{document_index}:{tool_index}:{usage.name}", source=item.source,
                name=usage.name, encoding=encoding, description=usage.description,
                schema_tokens=usage.schema, definition=usage.definition,
            ))
            if len(records) > settings.max_tools:
                raise ValueError(f"too many MCP tools; maximum is {settings.max_tools}")
    return McpResponse(
        method=method_info(encoding), records=records,
        totals={
            "description": sum(item.description for item in records),
            "schema": sum(item.schema_tokens for item in records),
            "definition": sum(item.definition for item in records),
        },
    )


def _matches(source: str, include: list[str], exclude: list[str]) -> bool:
    name = PurePosixPath(source).name
    included = not include or any(
        fnmatch.fnmatch(source, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in include
    )
    excluded = any(
        fnmatch.fnmatch(source, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in exclude
    )
    return included and not excluded


def estimate_context(request: ContextRequest, settings: Settings) -> ContextResponse:
    validate_content((item.content for item in request.items), settings)
    encoding = selected_encoding(request.encoding, settings)
    seen: set[str] = set()
    sources: list[TextSource] = []
    for item in request.items:
        source = normalize_relative_path(item.source)
        if source in seen:
            raise ValueError(f"duplicate source path: {source}")
        seen.add(source)
        if _matches(source, request.include, request.exclude):
            sources.append(TextSource(source, item.content))
    records = [
        ContextRecord(
            id=f"{index}:{item.source}", source=item.source, encoding=encoding,
            characters=item.characters, tokens=item.tokens,
        )
        for index, item in enumerate(measure_context_sources(sources, counter_for(encoding)))
    ]
    return ContextResponse(
        method=method_info(encoding), records=records,
        totals={
            "characters": sum(item.characters for item in records),
            "tokens": sum(item.tokens for item in records),
        },
    )


def estimate_scenario(request: ScenarioRequest, settings: Settings) -> ScenarioResponse:
    count = len(request.skills) + len(request.mcp_tools) + len(request.context)
    if count > settings.max_tools:
        raise ValueError(f"too many scenario components; maximum is {settings.max_tools}")
    encoding = selected_encoding(request.encoding, settings)
    component_encodings = (
        {item.record.encoding for item in request.skills}
        | {item.record.encoding for item in request.mcp_tools}
        | {item.record.encoding for item in request.context}
    )
    if component_encodings - {encoding}:
        raise ValueError(f"scenario components must use the selected encoding {encoding!r}")
    discovery = bodies = optional = 0
    for item in request.skills:
        if not item.installed:
            if item.body_active or item.active_optional_sources:
                raise ValueError(f"skill {item.record.name!r} cannot activate content when not installed")
            continue
        discovery += item.record.metadata
        bodies += item.record.body if item.body_active else 0
        available = {entry.source: entry.tokens for entry in item.record.optional_files}
        unknown = sorted(set(item.active_optional_sources) - available.keys())
        if unknown:
            raise ValueError(
                f"skill {item.record.name!r} has unknown optional source(s): {', '.join(unknown)}"
            )
        optional += sum(available[source] for source in set(item.active_optional_sources))
    breakdown = ScenarioBreakdown(
        skill_discovery=discovery, skill_bodies=bodies, skill_optional=optional,
        mcp_definitions=sum(item.record.definition for item in request.mcp_tools if item.included),
        context=sum(item.record.tokens for item in request.context if item.included),
    )
    return ScenarioResponse(
        method=method_info(encoding), breakdown=breakdown,
        total_tokens=sum((breakdown.skill_discovery, breakdown.skill_bodies,
                          breakdown.skill_optional, breakdown.mcp_definitions, breakdown.context)),
    )
