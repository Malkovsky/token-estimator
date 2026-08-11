# Agentic Token Estimator

Measure discovery metadata and full agentic content across skills, instructions, prompts, agents, and available MCP tool definitions in a public GitHub repository.

Paste a repository or folder URL to inventory its skills, instructions, rules, prompts, agents, and MCP configuration. The report separates lightweight discovery metadata from complete instructions and tool definitions, and it can be shared as an immutable commit URL.

## What you can do

- Analyze an entire public GitHub repository or one of its folders.
- Compare discovery metadata with full content totals by file, artifact type, and harness.
- Inspect skill instructions and their supporting text separately.
- Filter reports by path, artifact type, or compatible harness.
- Count a selected set of components with a configured Claude or Gemini model.
- Analyze pasted text or local files without uploading a repository.

Repository code is never executed. MCP configuration is treated as data, and sensitive configuration values are not included in reports.

## Usage

### Analyze a repository

Open the application, paste a public GitHub URL, and select **Analyze**.

```text
https://github.com/owner/repository
```

Short repository names work too:

```text
owner/repository
```

The application resolves the branch or tag to a commit and opens a report containing:

- discovery metadata tokens across artifact names and descriptions;
- full content tokens across recognized instructions, supporting text, and tool definitions;
- an inventory of loadable agentic artifacts;
- token counts for each artifact and component;
- the detected harness and loading behavior;
- scan warnings for skipped or unreadable content.

Discovery metadata is the compact identity a harness can use to decide whether an artifact is relevant. Full content includes that identity and all recognized instructions, supporting text, or canonical tool-definition fields. It is an inventory, not a claim that every artifact is loaded at the same time. Runtime MCP connection configuration is excluded.

| Artifact | Discovery metadata | Full content |
| --- | --- | --- |
| Skill | Name and description | Metadata, instructions, and recognized supporting files |
| MCP tool | Name and description | Metadata, schemas, title, and standard annotations |
| Agent | Name and description when declared | Complete agent definition |
| Rule or prompt | Name and description when declared | Complete file content |

### Analyze a folder

Paste a normal GitHub folder URL:

```text
https://github.com/owner/repository/tree/main/path/to/skills
```

You can also enter the repository and subdirectory separately under **Advanced options**. Folder reports use the same commit-level analysis as the full repository, so moving between folders does not download and scan the repository again while the report is cached.

### Add a token badge to a README

Repository reports show a combined token summary plus separate metadata and total-token badges, with a copy button next to each. Paste the resulting Markdown into a README:

```markdown
[![Token summary](https://how-much-tokens.onrender.com/badge/github/OWNER/REPOSITORY.svg?metric=summary)](https://how-much-tokens.onrender.com/)
[![Metadata tokens](https://how-much-tokens.onrender.com/badge/github/OWNER/REPOSITORY.svg?metric=metadata)](https://how-much-tokens.onrender.com/)
[![Total tokens](https://how-much-tokens.onrender.com/badge/github/OWNER/REPOSITORY.svg?metric=total)](https://how-much-tokens.onrender.com/)
```

Copied badges are pinned to the report's immutable commit. To make a badge follow a branch instead, use the branch name as `ref`, for example `ref=main` or `ref=release%2F2.x`. Each moving badge periodically resolves its branch head; branches at the same commit share one cached token summary.

Badge URLs follow the repository's default branch unless `ref` is supplied. They also accept the same optional `path` and `encoding` query parameters as repository analysis. The report action pins `ref` to the analyzed commit so its badge remains consistent with the linked report.

### Choose a tokenizer

The default baseline uses tiktoken's `o200k_base` encoding. Open **Advanced options** before analyzing to select `cl100k_base` instead.

Baseline counts are local and deterministic for the selected encoding. Provider-native counting is available only when the deployment has an allowed Claude or Gemini model configured.

### Analyze local content

Open **Local files** to measure content that is not in a public repository. The workbench accepts pasted text or selected files for:

- skills;
- MCP tool definitions;
- context and instruction files;
- combined loading scenarios.

Local file contents stay in the browser-to-server request and are not sent to model providers. Only an explicitly selected repository component can be submitted for provider-native counting.

## Run locally

The project requires Python 3.10 or later and Node.js 22 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
npm install --prefix frontend
```

Start the API from the repository root:

```bash
source .venv/bin/activate
uvicorn token_estimator_web.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another terminal, start the frontend:

```bash
npm run dev --prefix frontend
```

Open <http://localhost:5173>. The development server proxies API requests to port 8000, and interactive API documentation is available at <http://localhost:8000/docs>.

A GitHub token is optional, but anonymous requests receive GitHub's lower rate limit. If you set `GITHUB_TOKEN`, use a public-read-only token. Private repositories are rejected even when the token could access them.

### Run with Docker

```bash
docker build -t agentic-token-estimator .
docker run --rm -p 8000:8000 \
  -e TOKEN_ESTIMATOR_TURNSTILE_REQUIRED=false \
  -e TOKEN_ESTIMATOR_QUOTAS_ENABLED=false \
  agentic-token-estimator
```

Open <http://localhost:8000>. The production image serves both the API and the compiled frontend.

## API usage

Resolve a repository or folder URL first:

```bash
curl --request POST http://localhost:8000/api/v1/repositories/resolve \
  --header 'content-type: application/json' \
  --data '{
    "repository": "https://github.com/owner/repository/tree/main/path/to/skills",
    "encoding": "o200k_base"
  }'
```

The response contains a `canonical_path` pinned to the resolved commit. Send a `GET` request to that path to retrieve the report. Other useful endpoints are:

- `GET /api/v1/capabilities` — encodings, provider availability, and deployment limits.
- `GET /api/v1/repositories/github/{owner}/{repository}/commits/{sha}/progress` — server-sent cache, verification, tree, fetch, download, and analysis progress events.
- `POST /api/v1/token-counts/native` — provider-native count for selected report components.
- `POST /api/v1/estimates/skills` — skill files supplied as JSON.
- `POST /api/v1/estimates/mcp` — MCP definitions supplied as JSON.
- `POST /api/v1/estimates/context` — context files supplied as JSON.
- `POST /api/v1/scenarios/estimate` — a combined loading scenario.

See `/docs` for request and response schemas.

## Recognized repository content

The repository scanner recognizes common conventions for Codex, Claude Code, Gemini CLI, Cursor, GitHub Copilot, and generic `SKILL.md`-based skills. These include:

- `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` instruction files;
- `.claude/rules`, `.cursor/rules`, and `.github/instructions` rules;
- Claude, Cursor, Gemini, and GitHub Copilot commands, prompts, and agents;
- manifest-declared source-agent catalogs such as Agency Agents divisions;
- `.agents/skills`, `.claude/skills`, `.gemini/skills`, and other skill directories containing `SKILL.md`;
- `.codex/config.toml`, `.mcp.json`, and supported harness-specific MCP configuration;
- root-level `mcp-tools.json` snapshots containing generated MCP tool definitions.

MCP connection files are inventoried through a safe summary containing each server name and transport, but that connection summary is not counted as discovery metadata or full content. Commands, arguments, URLs, headers, environment variables, and credentials are not returned. Actual MCP tool names, descriptions, schemas, and supported definition details are counted when supplied through a committed `mcp-tools.json` snapshot or the local MCP workbench; they cannot be inferred from connection configuration without contacting or executing the server.

### Publish MCP tool definitions safely

MCP server maintainers are encouraged to generate and commit a root-level `mcp-tools.json`. This project-specific snapshot lets repository reports include the server's declared tool definitions without installing dependencies, executing repository code, contacting the server, or invoking a tool. MCP itself does not standardize this repository file.

```json
{
  "format": "mcp-tools-snapshot",
  "formatVersion": 1,
  "server": {
    "name": "example-mcp",
    "version": "1.0.0"
  },
  "tools": [
    {
      "name": "search",
      "title": "Search",
      "description": "Search indexed symbols.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": { "type": "string" }
        },
        "required": ["query"]
      },
      "outputSchema": {
        "type": "object",
        "properties": {
          "matches": { "type": "array", "items": { "type": "string" } }
        }
      },
      "annotations": { "readOnlyHint": true }
    }
  ]
}
```

The snapshot requires `format`, `formatVersion`, `server.name`, and `tools`; every tool requires a unique `name` and an object `inputSchema`. Copy the complete, sanitized result of all paginated `tools/list` responses into `tools`, preserving standard fields such as `title`, `description`, `outputSchema`, `annotations`, and `icons` when present. Do not commit the JSON-RPC envelope, cursors, `_meta`, credentials, runtime arguments, or tool results. Regenerate the snapshot in trusted local development or CI whenever tool definitions change.

Repository reports treat each tool's name and description as discovery metadata. Its canonical full definition contains the name, description, input schema, and—when present—the title, output schema, and standard MCP annotations. Examples and descriptions nested inside those schemas are therefore included. Icons, `_meta`, arbitrary extension fields, runtime results, and server connection details remain excluded.

Each MCP tool card shows discovery metadata, input schema, output and detail fields, and the full canonical definition. The explanatory parts are not additive because the full JSON definition also contains field names and serialization punctuation. When a supported MCP SDK dependency is detected but no valid snapshot exists, the report warns that the definitions cannot be estimated because the service does not execute the server or call `tools/list`.

## Caching and limits

Repository snapshots are fetched by immutable commit SHA. The service uses GitHub's recursive tree metadata to enforce repository-wide limits, then by default uses Git's partial-clone protocol to download only blobs that match recognized agentic-harness conventions, plus small root package manifests used to detect MCP SDK dependencies. It checks those exact blobs out sparsely, reads their original Git objects in one batch, and discards the temporary repository after analysis. If filtered Git is unavailable, `auto` mode falls back to the bounded GitHub archive path; set `TOKEN_ESTIMATOR_REPO_FETCH_MODE` to `git` or `archive` to require either behavior.

The resulting discovery data is held in a bounded in-memory cache by repository commit, independent of tokenizer encoding. Requesting another supported encoding reuses the retained component text and only reruns token counting; it does not fetch or parse the repository again. A folder selection filters the same full-repository result rather than creating a separate scan or cache entry.

By default, repository scans allow a 100 MiB compressed archive, 512 MiB of tree-reported repository blobs, 50,000 archive members, 5,000 relevant text files, and 20 MiB of decoded relevant content. At most two cold repository scans run concurrently and two more may wait for capacity; excess scans receive a retryable busy response. Repository work has a 120-second request lifetime, and abandoned work is cancelled once its last requester leaves. Full reports use a bounded two-hour memory cache. Badges additionally retain compact commit summaries for 30 days, capped globally and at 16 snapshots per repository; moving branch badges resolve their current commit before using that cache.

With quotas enabled, ref resolution has a separate global allowance of 1,000 uncached resolutions per hour. A normal repository workflow consumes one scan allowance only when it starts a cold commit analysis, not when it resolves a branch or tag. Cold scans default to 10 per client IP and 200 globally per hour. Badge scans share the global limit but do not use client-IP allowance because README image proxies commonly share addresses. Multi-key quota updates are atomic, so a rejected global request does not consume an IP allowance. Cache and scan limits can be changed with the `TOKEN_ESTIMATOR_REPORT_CACHE_*`, `TOKEN_ESTIMATOR_BADGE_CACHE_*`, and `TOKEN_ESTIMATOR_REPO_*` environment variables in [`backend/token_estimator_web/config.py`](backend/token_estimator_web/config.py).

## Deploy on Render

[`render.yaml`](render.yaml) defines a single Docker web service on Render's free plan. Create a Blueprint from the repository and configure:

- `TOKEN_ESTIMATOR_ALLOWED_ORIGINS` with the deployed origin;
- `GITHUB_TOKEN` with an optional public-read-only token;
- provider keys and model allowlists if native counting is required;
- Cloudflare Turnstile site and secret keys when exposing native counting publicly.

The free service sleeps when idle, and its in-memory cache is lost when the process restarts. The current cache and quota implementation assumes one process and one instance; use shared storage before scaling horizontally.

## Development

Run the test suites and production build from the repository root:

```bash
source .venv/bin/activate
pytest
npm test --prefix frontend
npm run build --prefix frontend
```

## License

Licensed under the [Apache License 2.0](LICENSE).
