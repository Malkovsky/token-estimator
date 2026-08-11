"""Versioned API request and response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MethodInfo(ApiModel):
    kind: Literal["tiktoken"] = "tiktoken"
    encoding: str
    version: str


class SkillFile(ApiModel):
    path: str = Field(min_length=1)
    content: str


class SkillsRequest(ApiModel):
    encoding: str | None = None
    files: list[SkillFile] = Field(min_length=1)


class McpDocument(ApiModel):
    source: str = Field(min_length=1)
    document: Any


class McpRequest(ApiModel):
    encoding: str | None = None
    documents: list[McpDocument] = Field(min_length=1)


class ContextItem(ApiModel):
    source: str = Field(min_length=1)
    content: str


class ContextRequest(ApiModel):
    encoding: str | None = None
    items: list[ContextItem] = Field(min_length=1)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class OptionalFileRecord(ApiModel):
    source: str
    tokens: int = Field(ge=0)


class SkillRecord(ApiModel):
    id: str
    source: str
    name: str
    declared_name: str
    encoding: str
    metadata: int = Field(ge=0)
    body: int = Field(ge=0)
    optional: int = Field(ge=0)
    optional_files: list[OptionalFileRecord]


class McpRecord(ApiModel):
    id: str
    source: str
    name: str
    encoding: str
    description: int = Field(ge=0)
    schema_tokens: int = Field(ge=0)
    definition: int = Field(ge=0)


class ContextRecord(ApiModel):
    id: str
    source: str
    encoding: str
    characters: int = Field(ge=0)
    tokens: int = Field(ge=0)


class SkillsResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    mode: Literal["skills"] = "skills"
    method: MethodInfo
    records: list[SkillRecord]
    totals: dict[str, int]


class McpResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    mode: Literal["mcp"] = "mcp"
    method: MethodInfo
    records: list[McpRecord]
    totals: dict[str, int]


class ContextResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    mode: Literal["context"] = "context"
    method: MethodInfo
    records: list[ContextRecord]
    totals: dict[str, int]


class ScenarioSkill(ApiModel):
    record: SkillRecord
    installed: bool = True
    body_active: bool = False
    active_optional_sources: list[str] = Field(default_factory=list)


class ScenarioMcp(ApiModel):
    record: McpRecord
    included: bool = True


class ScenarioContext(ApiModel):
    record: ContextRecord
    included: bool = True


class ScenarioRequest(ApiModel):
    encoding: str | None = None
    skills: list[ScenarioSkill] = Field(default_factory=list)
    mcp_tools: list[ScenarioMcp] = Field(default_factory=list)
    context: list[ScenarioContext] = Field(default_factory=list)


class ScenarioBreakdown(ApiModel):
    skill_discovery: int = Field(ge=0)
    skill_bodies: int = Field(ge=0)
    skill_optional: int = Field(ge=0)
    mcp_definitions: int = Field(ge=0)
    context: int = Field(ge=0)


class ScenarioResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    mode: Literal["scenario"] = "scenario"
    method: MethodInfo
    breakdown: ScenarioBreakdown
    total_tokens: int = Field(ge=0)


class RepositoryResolveRequest(ApiModel):
    repository: str = Field(min_length=1, max_length=500)
    ref: str | None = Field(default=None, max_length=250)
    subdirectory: str | None = Field(default=None, max_length=1000)
    encoding: str | None = None


class RepositoryIdentity(ApiModel):
    provider: Literal["github"] = "github"
    owner: str
    name: str
    commit_sha: str
    html_url: str
    subdirectory: str | None = None


class RepositoryResolveResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    repository: RepositoryIdentity
    requested_ref: str
    canonical_path: str


LoadPolicy = Literal[
    "progressive", "discovery", "activation", "on_demand",
    "hierarchical", "conditional", "configuration_only"
]
InventoryKind = Literal[
    "skill", "instruction", "rule", "prompt", "agent", "mcp_config", "mcp_tool"
]


class InventoryComponent(ApiModel):
    id: str
    path: str
    role: str
    load_policy: LoadPolicy
    characters: int = Field(ge=0)
    tokens: int = Field(ge=0)


class McpServerSummary(ApiModel):
    name: str
    transport: Literal["stdio", "http", "sse", "unknown"]


class McpToolBreakdown(ApiModel):
    name: int = Field(ge=0)
    description: int = Field(ge=0)
    input_schema: int = Field(ge=0)
    definition: int = Field(ge=0)


class InventoryItem(ApiModel):
    id: str
    path: str
    kind: InventoryKind
    name: str | None = None
    description: str | None = None
    harnesses: list[str]
    load_policy: LoadPolicy
    characters: int | None = Field(default=None, ge=0)
    tokens: int | None = Field(default=None, ge=0)
    components: list[InventoryComponent] = Field(default_factory=list)
    mcp_servers: list[McpServerSummary] = Field(default_factory=list)
    mcp_tool_breakdown: McpToolBreakdown | None = None
    accounting_note: str | None = None


class ScanWarning(ApiModel):
    code: str
    message: str
    path: str | None = None
    count: int = Field(default=1, ge=1)


class ScanStats(ApiModel):
    archive_members: int = Field(ge=0)
    relevant_files: int = Field(ge=0)
    relevant_bytes: int = Field(ge=0)


class RepositoryReport(ApiModel):
    api_version: Literal["v1"] = "v1"
    mode: Literal["repository"] = "repository"
    repository: RepositoryIdentity
    method: MethodInfo
    analyzer_version: str
    inventory: list[InventoryItem]
    metadata_tokens: int = Field(ge=0)
    category_totals: dict[str, int]
    warnings: list[ScanWarning]
    scan: ScanStats
    cached: bool = False


class NativeProviderCapability(ApiModel):
    id: Literal["anthropic", "gemini"]
    enabled: bool
    models: list[str]
    default_model: str | None = None


class CapabilitiesResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    sources: list[str]
    method: MethodInfo
    native_providers: list[NativeProviderCapability]
    turnstile_required: bool
    turnstile_site_key: str
    quotas_enabled: bool
    limits: dict[str, int]


class NativeSnapshot(ApiModel):
    owner: str
    repository: str
    commit_sha: str
    subdirectory: str | None = None
    encoding: str | None = None


class NativeCountRequest(ApiModel):
    provider: Literal["anthropic", "gemini"]
    model: str = Field(min_length=1, max_length=200)
    snapshot: NativeSnapshot
    item_ids: list[str] = Field(min_length=1)
    turnstile_token: str = Field(default="", max_length=2048)


class NativeCountResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    provider: Literal["anthropic", "gemini"]
    model: str
    item_ids: list[str]
    input_tokens: int = Field(ge=0)
    request_shape: Literal["raw-selection-v1"] = "raw-selection-v1"
    cached: bool = False


class EncodingsResponse(ApiModel):
    default: str
    encodings: list[str]
    tiktoken_version: str


class ErrorBody(ApiModel):
    code: str
    message: str
    details: list[Any] = Field(default_factory=list)


class ErrorResponse(ApiModel):
    error: ErrorBody
