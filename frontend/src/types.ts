export type MethodInfo = { kind: "tiktoken"; encoding: string; version: string };
export type ErrorEnvelope = { error: { code: string; message: string; details: unknown[] } };

export type InventoryComponent = {
  id: string; path: string; role: string; load_policy: string;
  characters: number; tokens: number;
};
export type InventoryItem = {
  id: string; path: string; kind: string; name: string | null; description: string | null;
  harnesses: string[]; load_policy: string;
  characters: number | null; tokens: number | null; components: InventoryComponent[];
  mcp_servers: { name: string; transport: string }[];
};
export type RepositoryReport = {
  api_version: "v1"; mode: "repository";
  repository: { provider: "github"; owner: string; name: string; commit_sha: string; html_url: string; subdirectory: string | null };
  method: MethodInfo; analyzer_version: string; inventory: InventoryItem[];
  metadata_tokens: number;
  category_totals: Record<string, number>;
  warnings: { code: string; message: string; path: string | null; count: number }[];
  scan: { archive_members: number; relevant_files: number; relevant_bytes: number };
  cached: boolean;
};
export type Capabilities = {
  method: MethodInfo; turnstile_required: boolean; turnstile_site_key: string;
  native_providers: { id: "anthropic" | "gemini"; enabled: boolean; models: string[]; default_model: string | null }[];
  limits: Record<string, number>;
};
export type SkillRecord = {
  id: string; source: string; name: string; declared_name: string; encoding: string;
  metadata: number; body: number; optional: number;
  optional_files: { source: string; tokens: number }[];
};
export type McpRecord = {
  id: string; source: string; name: string; encoding: string;
  description: number; schema_tokens: number; definition: number;
};
export type ContextRecord = {
  id: string; source: string; encoding: string; characters: number; tokens: number;
};
export type SkillsResponse = { mode: "skills"; method: MethodInfo; records: SkillRecord[]; totals: Record<string, number> };
export type McpResponse = { mode: "mcp"; method: MethodInfo; records: McpRecord[]; totals: Record<string, number> };
export type ContextResponse = { mode: "context"; method: MethodInfo; records: ContextRecord[]; totals: Record<string, number> };
export type ScenarioResponse = { mode: "scenario"; breakdown: Record<string, number>; total_tokens: number };
