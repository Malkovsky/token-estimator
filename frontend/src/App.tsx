import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { CANONICAL_DESIGN, designPath, designs, isDesignId, sampleRepository } from "./designs";
import type { DesignId, DesignScreen } from "./designs";
import type {
  Capabilities, ContextResponse, InventoryItem, McpResponse, RepositoryReport,
  ScenarioResponse, SkillsResponse,
} from "./types";

type BadgeMetric = "metadata" | "total" | "summary";
type BadgeStyle = "blueprint" | "classic" | "outline" | "capsule" | "terminal" | "paper" | "signal" | "mono" | "soft" | "minimal";
type RepositoryProgressEvent = {
  stage: "cache" | "verify" | "tree" | "fetch" | "download" | "analyze" | "complete" | "error";
  message: string; files?: number; loaded_bytes?: number; total_bytes?: number;
  code?: string; status?: number;
};

const repositoryProgressStages = [
  ["cache", "Cache"], ["verify", "Verify"], ["tree", "Tree"],
  ["fetch", "Commit"], ["download", "Files"], ["analyze", "Analyze"],
  ["complete", "Ready"],
] as const;

const badgeDesigns: { id: BadgeStyle; name: string; description: string; traits: string[] }[] = [
  { id: "blueprint", name: "Blueprint", description: "The current architectural blue system with the token glyph.", traits: ["Technical", "Branded", "Crisp"] },
  { id: "classic", name: "Classic Shields", description: "A familiar open-source badge with conventional proportions.", traits: ["Familiar", "Compact", "Neutral"] },
  { id: "outline", name: "Technical Outline", description: "White fields and precise blue rules for a lighter footprint.", traits: ["Light", "Precise", "Quiet"] },
  { id: "capsule", name: "Capsule", description: "A friendlier pill with the Blueprint gold accent on the result.", traits: ["Friendly", "Rounded", "Warm accent"] },
  { id: "terminal", name: "Terminal", description: "A dark developer-tool treatment with luminous green values.", traits: ["Developer", "Dark", "Monospace"] },
  { id: "paper", name: "Paper Label", description: "A warm editorial badge with square archival edges.", traits: ["Editorial", "Warm", "Serif"] },
  { id: "signal", name: "Signal", description: "High-contrast telemetry with neon value emphasis.", traits: ["Energetic", "Dark", "High contrast"] },
  { id: "mono", name: "Monochrome", description: "A restrained grayscale badge that fits almost any README.", traits: ["Universal", "Dense", "Neutral"] },
  { id: "soft", name: "Soft UI", description: "Pale blue surfaces with low visual weight and rounded edges.", traits: ["Gentle", "Modern", "Airy"] },
  { id: "minimal", name: "Minimal Line", description: "A nearly unfilled badge that lets the numbers do the work.", traits: ["Minimal", "Clean", "Low emphasis"] },
];

declare global {
  interface Window {
    turnstile?: {
      render: (target: string | HTMLElement, options: Record<string, unknown>) => string;
      reset: (widgetId: string) => void;
    };
  }
}

const number = (value: number | null | undefined) => value == null ? "—" : new Intl.NumberFormat().format(value);
const title = (value: string) => value.replaceAll("_", " ");
const badgeLabel = (metric: BadgeMetric) => metric === "summary" ? "Token summary" : metric === "metadata" ? "Minimal tokens" : "Full tokens";
const compactBytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 ** 2).toFixed(1)} MiB`;

function Shell({ children, design = CANONICAL_DESIGN, preview = false, screen = "landing" }: { children: React.ReactNode; design?: DesignId; preview?: boolean; screen?: DesignScreen }) {
  const home = preview || design !== CANONICAL_DESIGN ? designPath(design) : "/";
  const local = preview || design !== CANONICAL_DESIGN ? designPath(design, "local") : "/local";
  return <div className={`app-frame design-${design}`} data-design={design}>
    <header className="site-header">
      <a className="brand" href={home}>Agentic Token Estimator</a>
      <nav><a href={home}>Repository</a><a href={local}>Local files</a><a href="/docs">API</a></nav>
    </header>
    {preview && <DesignPreviewBar design={design} screen={screen} />}
    <main>{children}</main>
    <footer>Repository contents are fetched on demand and are not persisted.{preview && <> · Previewing {designs.find((item) => item.id === design)?.name}</>}</footer>
  </div>;
}

function DesignPreviewBar({ design, screen }: { design: DesignId; screen: DesignScreen }) {
  return <div className="design-preview-bar">
    <a className="back-to-gallery" href="/designs">← All designs</a>
    <div className="variant-tabs" aria-label="Design variant">{designs.map((item) => <a className={item.id === design ? "active" : ""} href={designPath(item.id, screen)} key={item.id}>{item.name}</a>)}</div>
    <div className="screen-tabs" aria-label="Preview screen">
      <a className={screen === "landing" ? "active" : ""} href={designPath(design)}>Landing</a>
      <a className={screen === "report" ? "active" : ""} href={designPath(design, "report")}>Report</a>
      <a className={screen === "local" ? "active" : ""} href={designPath(design, "local")}>Local</a>
    </div>
  </div>;
}

function DesignGallery() {
  return <Shell><section className="design-gallery-header">
    <p className="eyebrow">Five directions · one product</p>
    <h1>Choose how the estimator should feel.</h1>
    <p className="lede">Every concept uses the same content, API, states, and immutable sample report. Compare layout, density, hierarchy, and personality—not functionality.</p>
  </section>
  <section className="design-gallery">{designs.map((design, index) => <article className={`design-card design-card-${design.id}`} key={design.id}>
    <a className="design-thumbnail" href={designPath(design.id)} aria-label={`Open ${design.name}`}>
      <span className="mini-nav" /><span className="mini-title" /><span className="mini-input" /><span className="mini-grid"><i /><i /><i /></span>
    </a>
    <div className="design-card-copy"><span className="design-number">0{index + 1}</span><div><p>{design.direction}</p><h2>{design.name}</h2><p>{design.description}</p><div className="trait-list">{design.traits.map((trait) => <span key={trait}>{trait}</span>)}</div></div></div>
    <div className="design-card-actions"><a href={designPath(design.id)}>Landing</a><a href={designPath(design.id, "report")}>Sample report</a><a href={designPath(design.id, "local")}>Local tools</a></div>
  </article>)}</section></Shell>;
}

function BadgeDesignGallery() {
  return <Shell><section className="badge-gallery-header">
    <p className="eyebrow">Ten directions · three badge formats</p>
    <h1>Choose the badge system.</h1>
    <p className="lede">Every variant is a production-ready SVG using the same minimal and full token values. The existing Blueprint badge remains the default while you compare them.</p>
  </section>
  <section className="badge-design-gallery">{badgeDesigns.map((design, index) => <article className="badge-design-card" key={design.id}>
    <div className="badge-design-title"><span>{String(index + 1).padStart(2, "0")}</span><div><h2>{design.name}</h2><p>{design.description}</p></div></div>
    <div className={`badge-preview-surface badge-preview-${design.id}`}>
      <img src={`/badge/preview/${design.id}/summary.svg?v=4`} alt={`${design.name} token summary badge`} />
      <img src={`/badge/preview/${design.id}/metadata.svg?v=2`} alt={`${design.name} minimal token badge`} />
      <img src={`/badge/preview/${design.id}/total.svg?v=2`} alt={`${design.name} full token badge`} />
    </div>
    <div className="trait-list">{design.traits.map((trait) => <span key={trait}>{trait}</span>)}</div>
  </article>)}</section></Shell>;
}

function Home({ design = CANONICAL_DESIGN, preview = false }: { design?: DesignId; preview?: boolean }) {
  const [repository, setRepository] = useState("");
  const [ref, setRef] = useState("");
  const [subdirectory, setSubdirectory] = useState("");
  const [encoding, setEncoding] = useState("o200k_base");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api.resolve({
        repository, ref: ref || undefined, subdirectory: subdirectory || undefined, encoding,
      });
      const target = new URL(result.canonical_path, window.location.origin);
      if (design !== CANONICAL_DESIGN) target.searchParams.set("design", design);
      window.location.assign(`${target.pathname}${target.search}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not analyze repository"); }
    finally { setBusy(false); }
  }

  return <Shell design={design} preview={preview} screen="landing"><section className="hero">
    <h1>See what your agentic harness could load.</h1>
    <p className="lede">Paste a public GitHub repository or folder URL. We discover skills, instruction files, rules, prompts, agents, and MCP configuration without running repository code.</p>
    <form className="repo-form" onSubmit={submit}>
      <div className="repo-row">
        <input aria-label="GitHub repository or folder" autoFocus required value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="github.com/owner/repo/tree/main/skills" />
        <button disabled={busy}>{busy ? "Resolving…" : "Analyze"}</button>
      </div>
      <button className="text-button" type="button" onClick={() => setAdvanced(!advanced)}>{advanced ? "Hide" : "Show"} advanced options</button>
      {advanced && <div className="advanced-grid">
        <label>Branch, tag, or SHA<input value={ref} onChange={(event) => setRef(event.target.value)} placeholder="default branch" /></label>
        <label>Subdirectory<input value={subdirectory} onChange={(event) => setSubdirectory(event.target.value)} placeholder="optional/path" /></label>
        <label>Baseline encoding<select value={encoding} onChange={(event) => setEncoding(event.target.value)}><option>o200k_base</option><option>cl100k_base</option></select></label>
      </div>}
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  </section>
  <section className="secondary-callout"><div><h2>Need to measure custom content?</h2><p>Paste text or select local skill, MCP, and context files in the original workbench.</p></div><a className="secondary-button" href={preview || design !== CANONICAL_DESIGN ? designPath(design, "local") : "/local"}>Analyze local files</a></section>
  </Shell>;
}

function useTurnstile(capabilities: Capabilities | null) {
  const [token, setToken] = useState("");
  const widget = useRef<string | null>(null);
  useEffect(() => {
    if (!capabilities?.turnstile_required || !capabilities.turnstile_site_key) return;
    const render = () => {
      if (!window.turnstile || widget.current) return;
      widget.current = window.turnstile.render("#turnstile-widget", {
        sitekey: capabilities.turnstile_site_key,
        action: "native_count",
        callback: (value: string) => setToken(value),
        "expired-callback": () => setToken(""),
      });
    };
    const existing = document.querySelector<HTMLScriptElement>("script[data-turnstile]");
    if (existing) { if (window.turnstile) render(); else existing.addEventListener("load", render, { once: true }); return; }
    const script = document.createElement("script");
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.async = true; script.defer = true; script.dataset.turnstile = "true";
    script.addEventListener("load", render, { once: true }); document.head.appendChild(script);
  }, [capabilities]);
  const reset = () => { setToken(""); if (widget.current && window.turnstile) window.turnstile.reset(widget.current); };
  return { token, reset };
}

function InventoryCard({ item, selected, toggle }: { item: InventoryItem; selected: Set<string>; toggle: (id: string) => void }) {
  return <article className="inventory-card">
    <div className="inventory-heading"><div><div className="badges"><span>{title(item.kind)}</span>{item.harnesses.map((harness) => <span key={harness} className="muted-badge">{title(harness)}</span>)}</div><h3>{item.path}</h3>{item.description && <p className="metadata-description">{item.description}</p>}</div><strong>{number(item.tokens)} <small>tokens</small></strong></div>
    {item.mcp_tool_breakdown && <div className="mcp-breakdown">
      <span className="discovery-total"><small>Minimal</small><strong>{number(item.mcp_tool_breakdown.discovery)}</strong><em>Name {number(item.mcp_tool_breakdown.name)} · description {number(item.mcp_tool_breakdown.description)}</em></span>
      <span className="activation-total"><small>Expected</small><strong>{number(item.mcp_tool_breakdown.activation)}</strong><em>Canonical name, description, and input schema</em></span>
      <span><small>Optional context</small><strong>{number(item.mcp_tool_breakdown.output_schema + item.mcp_tool_breakdown.details)}</strong><em>Output schema {number(item.mcp_tool_breakdown.output_schema)} · details {number(item.mcp_tool_breakdown.details)}</em></span>
      <span className="definition-total"><small>Full</small><strong>{number(item.mcp_tool_breakdown.definition)}</strong></span>
    </div>}
    {item.components.length > 0 && <div className="components">{item.components.map((component) => <label key={component.id}>
      <input type="checkbox" checked={selected.has(component.id)} onChange={() => toggle(component.id)} />
      <span><strong>{title(component.role)}</strong><small>{component.path}</small></span><b>{number(component.tokens)}</b>
    </label>)}</div>}
    {item.mcp_servers.length > 0 && <div className="mcp-list">{item.mcp_servers.map((server) => <span key={server.name}>{server.name} · {server.transport}</span>)}</div>}
    {item.accounting_note && <p className="inventory-accounting-note">{item.accounting_note}</p>}
  </article>;
}

function RepositoryProgress({ progress, error = "" }: { progress: RepositoryProgressEvent; error?: string }) {
  const stageIndex = Math.max(0, repositoryProgressStages.findIndex(([id]) => id === progress.stage));
  const stageLabel = repositoryProgressStages[stageIndex]?.[1] || "Repository";
  const byteFraction = progress.stage === "download" && progress.total_bytes
    ? Math.min(0.9, (progress.loaded_bytes || 0) / progress.total_bytes * 0.9) : 0;
  const railProgress = Math.min(100, ((stageIndex + byteFraction) / (repositoryProgressStages.length - 1)) * 100);
  const detail = [
    progress.files != null ? `${number(progress.files)} relevant file${progress.files === 1 ? "" : "s"}` : "",
    progress.total_bytes != null
      ? progress.loaded_bytes != null && progress.loaded_bytes < progress.total_bytes
        ? `${compactBytes(progress.loaded_bytes)} of ${compactBytes(progress.total_bytes)}`
        : compactBytes(progress.total_bytes)
      : "",
  ].filter(Boolean).join(" · ");
  return <div className={`repository-progress${error ? " has-failed" : ""}`} aria-live="polite" aria-busy={!error}>
    <p className="eyebrow">Immutable snapshot analysis</p>
    <h1>{error ? "Repository analysis stopped" : "Mapping repository context…"}</h1>
    <div className="progress-rail" style={{ "--progress": `${railProgress}%` } as React.CSSProperties}>
      <div className="progress-track"><span /></div>
      <ol>{repositoryProgressStages.map(([id, label], index) => <li className={index < stageIndex ? "done" : index === stageIndex ? error ? "failed" : "current" : ""} key={id}>
        <span className="stage-node" aria-hidden="true">{index < stageIndex ? "✓" : index === stageIndex && error ? "!" : String(index + 1).padStart(2, "0")}</span>
        <span>{label}</span>
      </li>)}</ol>
    </div>
    <div className={`progress-current${error ? " failed" : ""}`}>
      <span className="progress-pulse" aria-hidden="true">{error ? "×" : ""}</span>
      <div><strong>{error ? `Stopped at ${stageLabel}` : progress.message}</strong>{error ? <small>{error}</small> : detail && <small>{detail}</small>}</div>
    </div>
    <p className="progress-note">Only recognized agentic-harness text is downloaded. Repository code is never executed.</p>
  </div>;
}

function ReportPage({ owner, repository, sha, design = CANONICAL_DESIGN, preview = false, search }: { owner: string; repository: string; sha: string; design?: DesignId; preview?: boolean; search?: string }) {
  const [report, setReport] = useState<RepositoryReport | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [kind, setKind] = useState("all");
  const [harness, setHarness] = useState("all");
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState<"anthropic" | "gemini">("anthropic");
  const [model, setModel] = useState("");
  const [nativeResult, setNativeResult] = useState<{ input_tokens: number; cached: boolean; model: string } | null>(null);
  const [busy, setBusy] = useState(true);
  const [nativeBusy, setNativeBusy] = useState(false);
  const [badgeCopied, setBadgeCopied] = useState<string | null>(null);
  const [progress, setProgress] = useState<RepositoryProgressEvent>({ stage: "cache", message: "Connecting to the repository analysis stream" });
  const [error, setError] = useState("");
  const { token, reset } = useTurnstile(capabilities);

  useEffect(() => {
    let active = true;
    let finished = false;
    const query = search ?? window.location.search;
    const capabilitiesRequest = api.capabilities();
    const source = new EventSource(api.reportProgressUrl(owner, repository, sha, query));
    const loadReport = () => {
      if (finished) return;
      finished = true;
      source.close();
      Promise.all([api.report(owner, repository, sha, query), capabilitiesRequest])
      .then(([nextReport, nextCapabilities]) => { if (active) {
        setReport(nextReport); setCapabilities(nextCapabilities);
        const enabled = nextCapabilities.native_providers.find((item) => item.enabled);
        if (enabled) { setProvider(enabled.id); setModel(enabled.default_model || ""); }
      }})
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load report"); })
      .finally(() => { if (active) setBusy(false); });
    };
    source.onmessage = (event) => {
      if (!active) return;
      try {
        const next = JSON.parse(event.data) as RepositoryProgressEvent;
        if (next.stage === "error") {
          finished = true; source.close(); setError(next.message); setBusy(false);
          return;
        }
        setProgress(next);
        if (next.stage === "complete") loadReport();
      } catch { source.close(); loadReport(); }
    };
    source.onerror = () => { if (active) loadReport(); };
    return () => { active = false; source.close(); };
  }, [owner, repository, sha, search]);

  const providerInfo = capabilities?.native_providers.find((item) => item.id === provider);
  useEffect(() => { if (providerInfo?.enabled && !providerInfo.models.includes(model)) setModel(providerInfo.default_model || ""); }, [providerInfo, model]);
  const kinds = useMemo(() => [...new Set(report?.inventory.map((item) => item.kind) || [])], [report]);
  const harnesses = useMemo(() => [...new Set(report?.inventory.flatMap((item) => item.harnesses) || [])], [report]);
  const filtered = useMemo(() => report?.inventory.filter((item) =>
    (kind === "all" || item.kind === kind) && (harness === "all" || item.harnesses.includes(harness)) && item.path.toLowerCase().includes(query.toLowerCase())
  ) || [], [report, kind, harness, query]);
  const badgeReference = new URLSearchParams(search ?? window.location.search).get("ref");
  const toggle = (id: string) => setSelected((current) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next; });

  async function nativeCount() {
    if (!report) return; setNativeBusy(true); setError("");
    try {
      const result = await api.nativeCount({
        provider, model,
        snapshot: {
          owner: report.repository.owner, repository: report.repository.name,
          commit_sha: report.repository.commit_sha, subdirectory: report.repository.subdirectory,
          encoding: report.method.encoding,
        },
        item_ids: [...selected], turnstile_token: token,
      });
      setNativeResult(result);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Native count failed"); }
    finally { setNativeBusy(false); reset(); }
  }

  function badgeUrl(metric: BadgeMetric) {
    if (!report) return "";
    const badge = new URL(`/badge/github/${encodeURIComponent(report.repository.owner)}/${encodeURIComponent(report.repository.name)}.svg`, window.location.origin);
    badge.searchParams.set("metric", metric);
    if (badgeReference) badge.searchParams.set("ref", badgeReference);
    badge.searchParams.set("encoding", report.method.encoding);
    badge.searchParams.set("v", `${report.analyzer_version}-badge3`);
    if (report.repository.subdirectory) badge.searchParams.set("path", report.repository.subdirectory);
    return badge.toString();
  }

  function badgeReportUrl() {
    if (!report) return "";
    const path = `/github/${encodeURIComponent(report.repository.owner)}/${encodeURIComponent(report.repository.name)}/latest`;
    const target = new URL(path, window.location.origin);
    if (badgeReference) target.searchParams.set("ref", badgeReference);
    target.searchParams.set("encoding", report.method.encoding);
    if (report.repository.subdirectory) target.searchParams.set("path", report.repository.subdirectory);
    return target.toString();
  }

  async function copyBadge(metric: BadgeMetric) {
    if (!report) return;
    const label = badgeLabel(metric);
    await navigator.clipboard.writeText(
      `[![${label}](${badgeUrl(metric)})](${badgeReportUrl()})`,
    );
    setBadgeCopied(metric);
    window.setTimeout(() => setBadgeCopied(null), 1800);
  }

  if (busy || (!report && error)) return <Shell design={design} preview={preview} screen="report"><RepositoryProgress progress={progress} error={error} /></Shell>;
  if (!report) return <Shell design={design} preview={preview} screen="report"><div className="state"><h1>Could not load this report</h1><p className="error">{error}</p><a href={preview ? designPath(design) : "/"}>Try another repository</a></div></Shell>;
  const enabledProviders = capabilities?.native_providers.filter((item) => item.enabled) || [];
  return <Shell design={design} preview={preview} screen="report"><section className="report-header">
    <div><p className="eyebrow">GitHub repository inventory</p><h1>{report.repository.owner} / {report.repository.name}</h1><p><code>{report.repository.commit_sha.slice(0, 12)}</code>{report.repository.subdirectory && <> · {report.repository.subdirectory}</>}</p></div>
    <div className="header-actions"><a href={report.repository.html_url} target="_blank" rel="noreferrer">View on GitHub</a><button className="secondary-button" onClick={() => navigator.clipboard.writeText(window.location.href)}>Copy report link</button></div>
  </section>
  <section className="metrics">
    <div><span>Full</span><strong>{number(report.category_totals.all_discovered_text)}</strong><small>reasonable upper bound</small></div>
    <div><span>Expected</span><strong>{number(report.activation_tokens)}</strong><small>practical default profile</small></div>
    <div><span>Minimal</span><strong>{number(report.metadata_tokens)}</strong><small>names + descriptions</small></div>
    <div><span>Inventory</span><strong>{number(report.inventory.length)}</strong><small>loadable artifacts</small></div>
    <div><span>Relevant files</span><strong>{number(report.scan.relevant_files)}</strong><small>{number(report.scan.relevant_bytes)} bytes</small></div>
    <div><span>Tokenizer</span><strong>{report.method.encoding}</strong><small>tiktoken {report.method.version}</small></div>
  </section>
  <section className="report-badges" aria-label="README badges">
    {(["summary", "metadata", "total"] as BadgeMetric[]).map((metric) => <div className="badge-share" key={metric}>
      <a className="badge-image-link" href={badgeReportUrl()} aria-label={`Open the latest report from the ${badgeLabel(metric).toLowerCase()} badge`}>
        <img src={badgeUrl(metric)} alt={`${badgeLabel(metric)} badge`} />
      </a>
      <div className="badge-actions">
        <button className="badge-copy" onClick={() => copyBadge(metric)}>{badgeCopied === metric ? "Copied" : "Copy"}</button>
      </div>
    </div>)}
  </section>
  <p className="badge-note">Copy follows the branch, tag, or commit supplied for this report; without one, it follows the repository’s default branch.</p>
  <p className="inventory-note">Minimal is the lower bound used to select artifacts. Expected is a practical default—not a guarantee of any particular harness—and includes skill instructions, MCP input schemas, and complete instruction, agent, rule, or prompt files. Full adds reasonable optional static content such as skill resources and MCP output schemas. These are repository-wide profiles, not estimates of one simultaneously active prompt.</p>
  <section className="report-grid"><div>
    <div className="filters"><input aria-label="Filter paths" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter paths…" /><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="all">All kinds</option>{kinds.map((value) => <option key={value}>{value}</option>)}</select><select value={harness} onChange={(event) => setHarness(event.target.value)}><option value="all">All harnesses</option>{harnesses.map((value) => <option key={value}>{value}</option>)}</select></div>
    <div className="inventory-list">{filtered.map((item) => <InventoryCard key={item.id} item={item} selected={selected} toggle={toggle} />)}{filtered.length === 0 && <p className="empty">No matching harness artifacts.</p>}</div>
  </div><aside className="native-panel">
    <p className="eyebrow">Provider-native count</p><h2>{selected.size} component{selected.size === 1 ? "" : "s"} selected</h2><p>Submit the selected raw text as one minimal provider request. This aggregate includes provider framing and is not additive per file.</p>
    {enabledProviders.length ? <>
      <label>Provider<select value={provider} onChange={(event) => { setProvider(event.target.value as "anthropic" | "gemini"); setNativeResult(null); }}>{enabledProviders.map((item) => <option key={item.id} value={item.id}>{item.id === "anthropic" ? "Claude" : "Gemini"}</option>)}</select></label>
      <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{providerInfo?.models.map((item) => <option key={item}>{item}</option>)}</select></label>
      {capabilities?.turnstile_required && <div id="turnstile-widget" className="turnstile" />}
      <button disabled={!selected.size || !model || nativeBusy || Boolean(capabilities?.turnstile_required && !token)} onClick={nativeCount}>{nativeBusy ? "Counting…" : "Count selected"}</button>
    </> : <p className="empty">Native providers are not configured on this deployment. Baseline counts remain available.</p>}
    {nativeResult && <div className="native-result"><strong>{number(nativeResult.input_tokens)}</strong><span>{nativeResult.model} input tokens{nativeResult.cached ? " · cached" : ""}</span></div>}
    {error && <p className="error" role="alert">{error}</p>}
  </aside></section>
  {report.warnings.length > 0 && <section className="warnings"><h2>Scan warnings</h2>{report.warnings.map((warning, index) => <p key={`${warning.code}-${index}`}><strong>{warning.code}{warning.count > 1 && <> × {number(warning.count)}</>}</strong>{warning.path && <> · example: {warning.path}</>} — {warning.message}</p>)}</section>}
  </Shell>;
}

type LocalMode = "skills" | "mcp" | "context" | "scenario";
function LocalWorkbench({ design = CANONICAL_DESIGN, preview = false }: { design?: DesignId; preview?: boolean }) {
  const [mode, setMode] = useState<LocalMode>("skills");
  const [encoding, setEncoding] = useState("o200k_base");
  const [text, setText] = useState("---\nname: example\ndescription: Example skill\n---\n\n# Instructions\n\nDo the work carefully.\n");
  const [files, setFiles] = useState<{ path: string; content: string }[]>([]);
  const [skills, setSkills] = useState<SkillsResponse | null>(null);
  const [mcp, setMcp] = useState<McpResponse | null>(null);
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [scenario, setScenario] = useState<ScenarioResponse | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");

  useEffect(() => {
    if (mode === "skills") setText("---\nname: example\ndescription: Example skill\n---\n\n# Instructions\n\nDo the work carefully.\n");
    if (mode === "mcp") setText('{"tools":[{"name":"search","description":"Search symbols","inputSchema":{"type":"object","properties":{"query":{"type":"string"}}}}]}');
    if (mode === "context") setText("Follow the repository instructions and keep changes focused.");
  }, [mode]);
  async function pick(event: React.ChangeEvent<HTMLInputElement>) {
    const selected = [...(event.target.files || [])];
    setFiles(await Promise.all(selected.map(async (file) => ({ path: file.webkitRelativePath || file.name, content: await file.text() }))));
  }
  async function run() {
    setBusy(true); setError("");
    try {
      if (mode === "skills") setSkills(await api.skills(files.length ? files : [{ path: "example/SKILL.md", content: text }], encoding));
      if (mode === "mcp") setMcp(await api.mcp(files.length ? files.map((file) => ({ source: file.path, document: JSON.parse(file.content) })) : [{ source: "tools.json", document: JSON.parse(text) }], encoding));
      if (mode === "context") setContext(await api.context(files.length ? files.map((file) => ({ source: file.path, content: file.content })) : [{ source: "context.txt", content: text }], encoding));
      if (mode === "scenario") setScenario(await api.scenario({
        encoding,
        skills: skills?.records.map((record) => ({ record, installed: true, body_active: true, active_optional_sources: record.optional_files.map((item) => item.source) })) || [],
        mcp_tools: mcp?.records.map((record) => ({ record, included: true })) || [],
        context: context?.records.map((record) => ({ record, included: true })) || [],
      }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Estimation failed"); }
    finally { setBusy(false); }
  }
  const result = mode === "skills" ? skills : mode === "mcp" ? mcp : mode === "context" ? context : scenario;
  return <Shell design={design} preview={preview} screen="local"><section className="local-header"><p className="eyebrow">Secondary workflow</p><h1>Analyze local files</h1><p>Nothing selected here is sent to a native model provider.</p></section>
    <section className="local-workbench"><nav className="tabs">{(["skills", "mcp", "context", "scenario"] as LocalMode[]).map((item) => <button className={mode === item ? "active" : ""} key={item} onClick={() => { setMode(item); setFiles([]); }}>{title(item)}</button>)}</nav>
      <div className="local-grid"><div className="editor-panel">
        {mode !== "scenario" ? <><textarea value={text} onChange={(event) => setText(event.target.value)} /><label className="file-picker">Or select UTF-8 files<input type="file" multiple onChange={pick} /></label>{files.length > 0 && <small>{files.length} file(s) selected</small>}</> : <p>The scenario combines the most recent skills, MCP, and context results. Skill bodies and optional text are treated as active.</p>}
        <div className="run-row"><select value={encoding} onChange={(event) => setEncoding(event.target.value)}><option>o200k_base</option><option>cl100k_base</option></select><button onClick={run} disabled={busy}>{busy ? "Estimating…" : mode === "scenario" ? "Build scenario" : "Estimate"}</button></div>
        {error && <p className="error">{error}</p>}
      </div><div className="local-results"><LocalResult result={result} /></div></div>
    </section></Shell>;
}

function LocalResult({ result }: { result: SkillsResponse | McpResponse | ContextResponse | ScenarioResponse | null }) {
  if (!result) return <div className="empty-state"><h2>No result yet</h2><p>Run an estimate to see its token breakdown.</p></div>;
  if (result.mode === "scenario") return <><div className="native-result"><strong>{number(result.total_tokens)}</strong><span>scenario tokens</span></div>{Object.entries(result.breakdown).map(([key, value]) => <div className="result-line" key={key}><span>{title(key)}</span><b>{number(value)}</b></div>)}</>;
  const total = result.mode === "skills" ? Object.values(result.totals).reduce((a, b) => a + b, 0) : result.mode === "mcp" ? result.totals.definition : result.totals.tokens;
  return <><div className="native-result"><strong>{number(total)}</strong><span>full {result.mode} context</span></div>{result.records.map((record) => {
    const full = "body" in record ? record.metadata + record.body + record.optional : "definition" in record ? record.definition : record.tokens;
    const discovery = "body" in record ? record.metadata : "definition" in record ? record.discovery_tokens : null;
    const activation = "body" in record ? record.metadata + record.body : "definition" in record ? record.activation_tokens : null;
    return <div className="result-line" key={record.id}><span>{"name" in record ? record.name : record.source}<small>{record.source}{discovery !== null && <> · minimal {number(discovery)} · expected {number(activation)}</>}</small></span><b>{number(full)}</b></div>;
  })}</>;
}

export default function App() {
  if (window.location.pathname === "/badge-designs" || window.location.pathname === "/badge-designs/") return <BadgeDesignGallery />;
  if (window.location.pathname === "/designs" || window.location.pathname === "/designs/") return <DesignGallery />;
  const designMatch = window.location.pathname.match(/^\/designs\/([^/]+)(?:\/(report|local))?\/?$/);
  if (designMatch && isDesignId(designMatch[1])) {
    const design = designMatch[1];
    if (designMatch[2] === "report") return <ReportPage {...sampleRepository} design={design} preview />;
    if (designMatch[2] === "local") return <LocalWorkbench design={design} preview />;
    return <Home design={design} preview />;
  }
  const requestedDesign = new URLSearchParams(window.location.search).get("design");
  const design: DesignId = isDesignId(requestedDesign) ? requestedDesign : CANONICAL_DESIGN;
  const match = window.location.pathname.match(/^\/github\/([^/]+)\/([^/]+)\/commit\/([0-9a-fA-F]{40,64})$/);
  if (match) return <ReportPage owner={decodeURIComponent(match[1])} repository={decodeURIComponent(match[2])} sha={match[3]} design={design} />;
  if (window.location.pathname === "/local") return <LocalWorkbench design={design} />;
  return <Home design={design} />;
}
