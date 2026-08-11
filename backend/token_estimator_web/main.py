"""FastAPI entrypoint and transport adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .badges import BadgeStyle, token_badge_svg
from .config import Settings
from .errors import ServiceProblem
from .middleware import (
    BodyLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware,
    client_ip, error_payload,
)
from .providers import NativeCountManager
from .repository import RepositoryManager
from .schemas import (
    CapabilitiesResponse, ContextItem, ContextRequest, ContextResponse,
    EncodingsResponse, ErrorResponse, McpDocument, McpRequest, McpResponse,
    NativeCountRequest, NativeCountResponse, NativeProviderCapability,
    RepositoryReport, RepositoryResolveRequest, RepositoryResolveResponse,
    ScenarioRequest, ScenarioResponse, SkillFile, SkillsRequest, SkillsResponse,
)
from .service import (
    available_encodings, estimate_context, estimate_mcp, estimate_scenario,
    estimate_skills, method_info,
)


settings = Settings.from_env()
estimate_slots = asyncio.Semaphore(settings.max_concurrent)
repositories = RepositoryManager(settings)
native_counts = NativeCountManager(settings, repositories)


@asynccontextmanager
async def lifespan(_: FastAPI):
    method_info(settings.default_encoding)
    try:
        yield
    finally:
        repositories.close()


app = FastAPI(
    title="Agentic Harness Token Estimator",
    version="2.0.0",
    description="Repository-first token inventory for skills and agentic harness configuration.",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    swagger_ui_oauth2_redirect_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "if-none-match"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, settings=settings)
app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)


@app.exception_handler(ServiceProblem)
async def service_problem_handler(_: Request, error: ServiceProblem) -> JSONResponse:
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    return JSONResponse(
        status_code=error.status, headers=headers,
        content=error_payload(error.code, error.message),
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    details = [{key: value for key, value in item.items() if key != "input"} for item in error.errors()]
    return JSONResponse(
        status_code=422,
        content=error_payload("invalid_request", "request validation failed", details),
    )


async def run_estimate(function: Callable[..., Any], *args: Any) -> Any:
    async def guarded() -> Any:
        async with estimate_slots:
            # Local/upload estimates are bounded to 10 MiB and complete quickly;
            # repository archive parsing uses its own worker thread below.
            return function(*args)
    try:
        return await asyncio.wait_for(guarded(), settings.request_timeout_seconds)
    except OverflowError as error:
        raise ServiceProblem(413, "content_too_large", str(error)) from error
    except ValueError as error:
        raise ServiceProblem(422, "invalid_input", str(error)) from error
    except RuntimeError as error:
        raise ServiceProblem(503, "estimator_unavailable", str(error)) from error
    except asyncio.TimeoutError as error:
        raise ServiceProblem(503, "estimator_busy", "estimation timed out") from error


async def _read_uploads(files: list[UploadFile], paths: list[str] | None) -> list[tuple[str, str]]:
    if not files:
        raise ServiceProblem(422, "missing_files", "at least one file is required")
    if len(files) > settings.max_items:
        raise ServiceProblem(413, "too_many_items", f"maximum is {settings.max_items} files")
    if paths and len(paths) != len(files):
        raise ServiceProblem(422, "invalid_manifest", "paths must match uploaded files")
    resolved = paths or [item.filename or f"upload-{index}" for index, item in enumerate(files)]
    if any(Path(item).suffix.lower() == ".zip" for item in resolved):
        raise ServiceProblem(415, "archives_not_supported", "ZIP archives are not supported")
    total = 0
    decoded: list[tuple[str, str]] = []
    for path, upload in zip(resolved, files):
        chunks: list[bytes] = []
        while chunk := await upload.read(64 * 1024):
            total += len(chunk)
            if total > settings.max_content_bytes:
                raise ServiceProblem(413, "content_too_large", "decoded content exceeds the configured limit")
            chunks.append(chunk)
        try:
            decoded.append((path, b"".join(chunks).decode("utf-8", errors="strict")))
        except UnicodeDecodeError as error:
            raise ServiceProblem(415, "invalid_utf8", f"{path}: not a UTF-8 text file") from error
    return decoded


ERROR_RESPONSES = {code: {"model": ErrorResponse} for code in (403, 404, 413, 415, 422, 429, 502, 503)}


@app.get("/docs", include_in_schema=False)
async def swagger_docs() -> Response:
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - API docs",
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/healthz", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["operations"])
async def ready() -> dict[str, str]:
    await run_estimate(method_info, settings.default_encoding)
    return {"status": "ready"}


@app.get("/api/v1/encodings", response_model=EncodingsResponse, tags=["metadata"])
async def encodings() -> EncodingsResponse:
    import tiktoken
    return EncodingsResponse(
        default=settings.default_encoding, encodings=list(available_encodings()),
        tiktoken_version=tiktoken.__version__,
    )


@app.get("/api/v1/capabilities", response_model=CapabilitiesResponse, tags=["metadata"])
async def capabilities() -> CapabilitiesResponse:
    providers = []
    for provider, key, models in (
        ("anthropic", settings.anthropic_api_key, settings.anthropic_models),
        ("gemini", settings.gemini_api_key, settings.gemini_models),
    ):
        enabled = bool(key and models)
        providers.append(NativeProviderCapability(
            id=provider, enabled=enabled, models=list(models) if enabled else [],
            default_model=models[0] if enabled else None,
        ))
    return CapabilitiesResponse(
        sources=["github"], method=method_info(settings.default_encoding),
        native_providers=providers, turnstile_required=settings.turnstile_required,
        turnstile_site_key=settings.turnstile_site_key,
        quotas_enabled=settings.quotas_enabled,
        limits={
            "repository_files": settings.repo_max_relevant_files,
            "repository_content_bytes": settings.repo_max_content_bytes,
            "native_items": settings.native_max_items,
            "native_content_bytes": settings.native_max_bytes,
        },
    )


@app.post(
    "/api/v1/repositories/resolve", response_model=RepositoryResolveResponse,
    responses=ERROR_RESPONSES, tags=["repositories"],
)
async def resolve_repository(request: RepositoryResolveRequest, raw: Request) -> RepositoryResolveResponse:
    return await repositories.resolve(request, client_ip(raw, settings))


@app.get(
    "/api/v1/repositories/github/{owner}/{repository}/commits/{sha}",
    response_model=RepositoryReport, responses=ERROR_RESPONSES, tags=["repositories"],
)
async def repository_report(
    owner: str, repository: str, sha: str, raw: Request,
    path: str | None = None, encoding: str | None = None,
) -> Response:
    analysis = await repositories.get(
        owner, repository, sha, path, encoding, client_ip(raw, settings)
    )
    etag_value = hashlib.sha256(
        f"{owner}|{repository}|{sha}|{path}|{encoding}|{settings.analyzer_version}".encode()
    ).hexdigest()
    etag = f'"{etag_value}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=600, must-revalidate"}
    if raw.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=analysis.report.model_dump(mode="json"), headers=headers)


@app.get(
    "/badge/github/{owner}/{repository}.svg",
    responses={200: {"content": {"image/svg+xml": {}}}},
    tags=["badges"],
)
async def repository_badge(
    owner: str, repository: str, raw: Request,
    ref: str | None = None, path: str | None = None, encoding: str | None = None,
    metric: Literal["metadata", "total"] = "total",
    style: BadgeStyle = "blueprint",
) -> Response:
    """Render a README badge for the selected repository snapshot."""
    ip = client_ip(raw, settings)
    try:
        summary = await repositories.badge_summary(
            owner, repository, ref, path, encoding, ip
        )
        tokens = (
            summary.metadata_tokens
            if metric == "metadata"
            else summary.total_tokens
        )
        label = "metadata tokens" if metric == "metadata" else "total tokens"
        cache_control = (
            "public, max-age=300, stale-while-revalidate=3600"
            if summary.mutable_ref
            else "public, max-age=86400, stale-while-revalidate=604800"
        )
        return Response(
            content=token_badge_svg(tokens, label, style), media_type="image/svg+xml",
            headers={
                "Cache-Control": cache_control,
                "X-Repository-Commit": summary.commit_sha,
                "X-Badge-Metric": metric,
            },
        )
    except ServiceProblem:
        return Response(
            content=token_badge_svg(
                label="metadata tokens" if metric == "metadata" else "total tokens",
                style=style,
            ),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )


@app.get("/badge/preview/{style}/{metric}.svg", include_in_schema=False)
async def badge_preview(
    style: BadgeStyle, metric: Literal["metadata", "total"],
) -> Response:
    """Render deterministic samples for the local badge design gallery."""
    tokens = 11_270 if metric == "metadata" else 814_027
    label = "metadata tokens" if metric == "metadata" else "total tokens"
    return Response(
        content=token_badge_svg(tokens, label, style), media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.post(
    "/api/v1/token-counts/native", response_model=NativeCountResponse,
    responses=ERROR_RESPONSES, tags=["native-counts"],
)
async def native_count(request: NativeCountRequest, raw: Request) -> NativeCountResponse:
    return await native_counts.count(request, client_ip(raw, settings))


@app.post("/api/v1/estimates/skills", response_model=SkillsResponse, responses=ERROR_RESPONSES, tags=["local"])
async def skills(request: SkillsRequest) -> SkillsResponse:
    return await run_estimate(estimate_skills, request, settings)


@app.post("/api/v1/estimates/skills/upload", response_model=SkillsResponse, responses=ERROR_RESPONSES, tags=["local"])
async def skills_upload(
    files: Annotated[list[UploadFile], File()],
    encoding: Annotated[str | None, Form()] = None,
    paths: Annotated[list[str] | None, Form()] = None,
) -> SkillsResponse:
    decoded = await _read_uploads(files, paths)
    return await run_estimate(
        estimate_skills,
        SkillsRequest(encoding=encoding, files=[SkillFile(path=path, content=content) for path, content in decoded]),
        settings,
    )


@app.post("/api/v1/estimates/mcp", response_model=McpResponse, responses=ERROR_RESPONSES, tags=["local"])
async def mcp(request: McpRequest) -> McpResponse:
    return await run_estimate(estimate_mcp, request, settings)


@app.post("/api/v1/estimates/mcp/upload", response_model=McpResponse, responses=ERROR_RESPONSES, tags=["local"])
async def mcp_upload(
    files: Annotated[list[UploadFile], File()],
    encoding: Annotated[str | None, Form()] = None,
    paths: Annotated[list[str] | None, Form()] = None,
) -> McpResponse:
    decoded = await _read_uploads(files, paths)
    documents: list[McpDocument] = []
    for path, content in decoded:
        try:
            document = json.loads(content)
        except json.JSONDecodeError as error:
            raise ServiceProblem(422, "invalid_json", f"{path}: invalid JSON") from error
        documents.append(McpDocument(source=path, document=document))
    return await run_estimate(estimate_mcp, McpRequest(encoding=encoding, documents=documents), settings)


@app.post("/api/v1/estimates/context", response_model=ContextResponse, responses=ERROR_RESPONSES, tags=["local"])
async def context(request: ContextRequest) -> ContextResponse:
    return await run_estimate(estimate_context, request, settings)


@app.post("/api/v1/estimates/context/upload", response_model=ContextResponse, responses=ERROR_RESPONSES, tags=["local"])
async def context_upload(
    files: Annotated[list[UploadFile], File()],
    encoding: Annotated[str | None, Form()] = None,
    paths: Annotated[list[str] | None, Form()] = None,
    include: Annotated[list[str] | None, Form()] = None,
    exclude: Annotated[list[str] | None, Form()] = None,
) -> ContextResponse:
    decoded = await _read_uploads(files, paths)
    request = ContextRequest(
        encoding=encoding,
        items=[ContextItem(source=path, content=content) for path, content in decoded],
        include=include or [], exclude=exclude or [],
    )
    return await run_estimate(estimate_context, request, settings)


@app.post("/api/v1/scenarios/estimate", response_model=ScenarioResponse, responses=ERROR_RESPONSES, tags=["local"])
async def scenario(request: ScenarioRequest) -> ScenarioResponse:
    return await run_estimate(estimate_scenario, request, settings)


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.is_dir():
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = (DIST / full_path).resolve()
        if DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def api_root() -> dict[str, str]:
        return {"name": "Agentic Harness Token Estimator", "docs": "/docs"}
