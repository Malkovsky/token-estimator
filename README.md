# Agentic Token Estimator

Estimate how many tokens an agentic harness can discover in a public GitHub repository.

Paste a repository or folder URL to inventory its skills, instructions, rules, prompts, agents, and MCP configuration. The report shows baseline token counts for every discovered artifact and can be shared as an immutable commit URL.

## What you can do

- Analyze an entire public GitHub repository or one of its folders.
- See token totals by file, artifact type, and harness.
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

- total tokens across discovered repository text;
- an inventory of loadable agentic artifacts;
- token counts for each artifact and component;
- the detected harness and loading behavior;
- scan warnings for skipped or unreadable content.

The headline total is an inventory of discovered text, not a claim that every file is loaded into one prompt at the same time.

### Analyze a folder

Paste a normal GitHub folder URL:

```text
https://github.com/owner/repository/tree/main/path/to/skills
```

You can also enter the repository and subdirectory separately under **Advanced options**. Folder reports use the same commit-level analysis as the full repository, so moving between folders does not download and scan the repository again while the report is cached.

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
- `.agents/skills`, `.claude/skills`, `.gemini/skills`, and other skill directories containing `SKILL.md`;
- `.codex/config.toml`, `.mcp.json`, and supported harness-specific MCP configuration.

## Caching and limits

Repository archives are downloaded by immutable commit SHA, inspected without extraction, and discarded after analysis. The resulting analysis is held in a bounded in-memory cache by repository commit and tokenizer encoding. A folder selection filters that full-repository result rather than creating a separate scan.

By default, repository scans accept up to 5,000 relevant text files and 20 MiB of decoded relevant content. Cache and scan limits can be changed with the `TOKEN_ESTIMATOR_REPORT_CACHE_*` and `TOKEN_ESTIMATOR_REPO_*` environment variables in [`backend/token_estimator_web/config.py`](backend/token_estimator_web/config.py).

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
