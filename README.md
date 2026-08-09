# Agentic Token Estimator

A repository-first web application that inventories token-bearing agentic harness files in public GitHub repositories. It discovers skills, instruction files, rules, prompts, agents, and safe MCP configuration metadata without cloning the repository or executing its code.

The original upload/paste estimator is retained at `/local`.

## Local development

Run all commands from the repository root.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
npm install --prefix frontend
```

Start the API:

```bash
source .venv/bin/activate
uvicorn token_estimator_web.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another terminal, start the frontend:

```bash
npm run dev --prefix frontend
```

Open `http://localhost:5173`. The frontend proxies `/api` to port 8000.

Repository analysis works without a GitHub token at GitHub's lower anonymous rate limit. For practical use, set a read-only token that cannot access private repositories. The application rejects private repositories even when credentials could read them.

Native Claude and Gemini counting is disabled unless its API key and model allowlist are both configured. For local work without native providers, copy `.env.example` values into your shell and leave provider keys empty. Local file content is never sent to native providers.

For unrestricted local testing, set `TOKEN_ESTIMATOR_QUOTAS_ENABLED=false`. This removes request, repository, and native-provider quotas while retaining content-size, timeout, and concurrency limits. Quotas remain enabled by default when the variable is omitted.

## API

- `POST /api/v1/repositories/resolve` accepts `owner/repository`, a repository URL, or a GitHub `/tree/<ref>/<folder>` URL and resolves it to an immutable commit report URL.
- `GET /api/v1/repositories/github/{owner}/{repository}/commits/{sha}` returns the repository inventory.
- `GET /api/v1/capabilities` reports enabled providers and deployment limits.
- `POST /api/v1/token-counts/native` counts selected repository component IDs with Claude or Gemini.
- Existing `/api/v1/estimates/*` and `/api/v1/scenarios/estimate` endpoints power `/local`.

Repository archives are downloaded by SHA, bounded before analysis, inspected without extraction, and discarded immediately after analysis. The resulting repository analysis is retained only in the bounded in-memory cache. MCP configuration is never executed and sensitive configuration fields are never returned.

Repository scans allow up to 5,000 relevant text files and 20 MiB of decoded relevant content by default, independently of the smaller local-upload limits. Override these with `TOKEN_ESTIMATOR_REPO_MAX_RELEVANT_FILES` and `TOKEN_ESTIMATOR_REPO_MAX_CONTENT_BYTES` when deploying with different resource constraints.

Repository reports are cached once per immutable commit and encoding. An optional `path` filters that cached full-repository analysis, so browsing another folder at the same commit does not download or analyze the repository again.

## Tests and production build

```bash
source .venv/bin/activate
pytest
npm test --prefix frontend
npm run build --prefix frontend
docker build -t agentic-token-estimator .
docker run --rm -p 8000:8000 -e TOKEN_ESTIMATOR_TURNSTILE_REQUIRED=false agentic-token-estimator
```

## Render beta

`render.yaml` defines a single Docker web service. Before deployment, set:

- `TOKEN_ESTIMATOR_ALLOWED_ORIGINS` to the final `https://...onrender.com` or custom origin.
- A public-read-only `GITHUB_TOKEN`.
- Provider keys and comma-separated model allowlists if native counting is enabled.
- Cloudflare Turnstile site/secret keys and `TURNSTILE_EXPECTED_HOSTNAME`.

The beta intentionally uses one process and one instance because cache and quota state is in memory. Add a shared Redis-backed implementation before scaling horizontally.

The Blueprint explicitly selects Render's free instance plan. Free services sleep when idle and lose their in-memory cache whenever the process restarts.

## Source layout

The standalone application is entirely contained in this directory. `agentic/ai_for_cpp` is a preserved reference checkout and is excluded from application imports and Docker builds.
