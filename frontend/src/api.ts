import type {
  Capabilities, ContextResponse, ErrorEnvelope, McpResponse, RepositoryReport,
  ScenarioResponse, SkillsResponse,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = ((await response.json()) as ErrorEnvelope).error.message; } catch { /* HTTP text fallback */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
});

export const api = {
  capabilities: () => request<Capabilities>("/api/v1/capabilities"),
  resolve: (body: { repository: string; ref?: string; subdirectory?: string; encoding?: string }) =>
    request<{ canonical_path: string }>("/api/v1/repositories/resolve", json(body)),
  report: (owner: string, repository: string, sha: string, search: string) =>
    request<RepositoryReport>(`/api/v1/repositories/github/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/commits/${sha}${search}`),
  reportProgressUrl: (owner: string, repository: string, sha: string, search: string) =>
    `/api/v1/repositories/github/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/commits/${sha}/progress${search}`,
  nativeCount: (body: unknown) => request<{ input_tokens: number; cached: boolean; model: string }>("/api/v1/token-counts/native", json(body)),
  skills: (files: { path: string; content: string }[], encoding: string) =>
    request<SkillsResponse>("/api/v1/estimates/skills", json({ files, encoding })),
  mcp: (documents: { source: string; document: unknown }[], encoding: string) =>
    request<McpResponse>("/api/v1/estimates/mcp", json({ documents, encoding })),
  context: (items: { source: string; content: string }[], encoding: string) =>
    request<ContextResponse>("/api/v1/estimates/context", json({ items, encoding })),
  scenario: (body: unknown) => request<ScenarioResponse>("/api/v1/scenarios/estimate", json(body)),
};
