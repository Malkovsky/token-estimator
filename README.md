# Agentic Token Estimator

Count prompt-facing tokens across skills, instructions, prompts, agents, and available MCP tool definitions in a public GitHub repository.

Paste a repository or folder URL to inventory its skills, instructions, rules, prompts, agents, and MCP configuration. The report separates prompt-facing token counts from runtime connection configuration and can be shared as an immutable commit URL.

## What you can do

- Analyze an entire public GitHub repository or one of its folders.
- See prompt-facing token totals by file, artifact type, and harness.
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

- total tokens across discovered prompt-facing repository text;
- an inventory of loadable agentic artifacts;
- token counts for each artifact and component;
- the detected harness and loading behavior;
- scan warnings for skipped or unreadable content.

The headline total is an inventory of discovered prompt-facing text, not a claim that every file is loaded into one prompt at the same time. Runtime MCP connection configuration is excluded.

### Analyze a folder

Paste a normal GitHub folder URL:

```text
https://github.com/owner/repository/tree/main/path/to/skills
```

You can also enter the repository and subdirectory separately under **Advanced options**. Folder reports use the same commit-level analysis as the full repository, so moving between folders does not download and scan the repository again while the report is cached.

### Add a token badge to a README

Repository reports show a combined token summary plus separate metadata and total-token badges, with a copy button next to each. Paste the resulting Markdown into a README:

```markdown
[![Token summary](https://agentic-token-estimator.onrender.com/badge/github/OWNER/REPOSITORY.svg?metric=summary)](https://agentic-token-estimator.onrender.com/)
[![Metadata tokens](https://agentic-token-estimator.onrender.com/badge/github/OWNER/REPOSITORY.svg?metric=metadata)](https://agentic-token-estimator.onrender.com/)
[![Total tokens](https://agentic-token-estimator.onrender.com/badge/github/OWNER/REPOSITORY.svg?metric=total)](https://agentic-token-estimator.onrender.com/)
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
- `.codex/config.toml`, `.mcp.json`, and supported harness-specific MCP configuration.

MCP connection files are inventoried without treating their raw contents as model context. Reports expose and count only a canonical metadata summary containing each server name and transport. Commands, arguments, URLs, headers, environment variables, and credentials are not returned and do not contribute to prompt-facing totals. Actual MCP tool names, descriptions, and input schemas are counted when supplied to the local MCP workbench; they cannot be inferred from connection configuration without contacting or executing the server.

## Caching and limits

Repository snapshots are fetched by immutable commit SHA. The service uses GitHub's recursive tree metadata to enforce repository-wide limits, then by default uses Git's partial-clone protocol to download only blobs that match recognized agentic-harness conventions. It checks those exact blobs out sparsely, reads their original Git objects in one batch, and discards the temporary repository after analysis. If filtered Git is unavailable, `auto` mode falls back to the bounded GitHub archive path; set `TOKEN_ESTIMATOR_REPO_FETCH_MODE` to `git` or `archive` to require either behavior.

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
