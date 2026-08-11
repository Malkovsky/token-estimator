from __future__ import annotations

import asyncio
import io
import tarfile
import tempfile
from dataclasses import replace

import httpx
import pytest

from token_estimator_web.badges import BADGE_STYLES, compact_tokens, token_badge_svg
from token_estimator_web.cache import AsyncTTLCache
from token_estimator_web.config import Settings
from token_estimator_web.main import app, repositories
from token_estimator_web.providers import NativeCountManager
from token_estimator_web.errors import ServiceProblem
from token_estimator_web.repository import GitHubGateway, RepositoryManager, analyze_archive, parse_repository
from token_estimator_web.schemas import (
    NativeCountRequest, NativeSnapshot, RepositoryIdentity, RepositoryResolveRequest,
    RepositoryResolveResponse,
)


SKILL = """---
name: demo
description: Demonstrate a compact workflow.
---

# Demo

Follow the workflow.
"""


def archive(files: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for path, content in files.items():
            info = tarfile.TarInfo(f"owner-repo-sha/{path}")
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
        for path, target in (links or {}).items():
            info = tarfile.TarInfo(f"owner-repo-sha/{path}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            bundle.addfile(info)
    return output.getvalue()


def request(method: str, path: str, **kwargs: object) -> httpx.Response:
    async def execute() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(execute())


def test_health_capabilities_and_legacy_context() -> None:
    assert request("GET", "/healthz").json() == {"status": "ok"}
    docs = request("GET", "/docs")
    assert docs.status_code == 200
    assert "swagger-ui-bundle.js" in docs.text
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    capabilities = request("GET", "/api/v1/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["sources"] == ["github"]
    response = request(
        "POST", "/api/v1/estimates/context",
        json={"items": [{"source": "AGENTS.md", "content": "Follow the rules."}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["records"][0]["tokens"] > 0


def test_token_badge_formatting() -> None:
    assert compact_tokens(999) == "999"
    assert compact_tokens(1_250) == "1.2k"
    assert compact_tokens(572_949) == "573k"
    assert compact_tokens(1_250_000) == "1.2M"
    svg = token_badge_svg(572_949, "total tokens")
    assert 'aria-label="total tokens: 573k"' in svg
    assert "#145eb5" in svg
    assert "unavailable" in token_badge_svg(label="metadata tokens")
    for style in BADGE_STYLES:
        styled = token_badge_svg(11_270, "metadata tokens", style)
        assert 'aria-label="metadata tokens: 11k"' in styled
        assert styled.startswith("<svg")
        if style not in {"classic", "minimal"}:
            assert '<rect width="32"' in styled
            assert 'd="M9 5H6v10h3M23 5h3v10h-3"' in styled
        if style in {"blueprint", "outline", "capsule", "soft"}:
            assert 'stroke="#ffcc55"' in styled


def test_badge_design_preview_endpoints() -> None:
    for style in BADGE_STYLES:
        response = request("GET", f"/badge/preview/{style}/metadata.svg")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert "metadata tokens: 11k" in response.text


def test_legacy_skill_mcp_and_scenario() -> None:
    skill = request(
        "POST", "/api/v1/estimates/skills",
        json={"files": [{"path": "demo/SKILL.md", "content": SKILL}]},
    ).json()
    mcp = request(
        "POST", "/api/v1/estimates/mcp",
        json={"documents": [{"source": "tools.json", "document": {
            "tools": [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}]
        }}]},
    ).json()
    scenario = request(
        "POST", "/api/v1/scenarios/estimate",
        json={
            "skills": [{"record": skill["records"][0], "body_active": True}],
            "mcp_tools": [{"record": mcp["records"][0]}],
        },
    )
    assert scenario.status_code == 200
    assert scenario.json()["total_tokens"] > skill["records"][0]["metadata"]


def test_repository_locator_validation() -> None:
    assert parse_repository("openai/codex") == ("openai", "codex")
    assert parse_repository("https://github.com/openai/codex.git") == ("openai", "codex")
    assert parse_repository("https://github.com/openai/codex/tree/main/codex-rs") == ("openai", "codex")
    with pytest.raises(Exception):
        parse_repository("https://example.com/openai/codex")
    with pytest.raises(Exception):
        parse_repository("https://github.com/openai/codex/blob/main/README.md")


def test_resolve_creates_commit_canonical_path_and_rejects_private(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.from_env()
    gateway = GitHubGateway(settings)

    async def public_json(path: str) -> dict[str, object]:
        if "/commits/" in path:
            return {"sha": "d" * 40}
        return {"private": False, "visibility": "public", "default_branch": "main", "name": "Repo", "owner": {"login": "Acme"}}

    monkeypatch.setattr(gateway, "_json", public_json)
    resolved = asyncio.run(gateway.resolve(
        RepositoryResolveRequest(repository="acme/repo", subdirectory="skills"), settings
    ))
    assert resolved.repository.commit_sha == "d" * 40
    assert resolved.canonical_path.startswith("/github/Acme/Repo/commit/")
    assert "path=skills" in resolved.canonical_path

    folder = asyncio.run(gateway.resolve(
        RepositoryResolveRequest(
            repository="https://github.com/acme/repo/tree/release/skills/demo"
        ), settings
    ))
    assert folder.requested_ref == "release"
    assert folder.repository.subdirectory == "skills/demo"
    assert "path=skills%2Fdemo" in folder.canonical_path
    assert folder.repository.html_url.endswith("/skills/demo")

    async def private_json(_: str) -> dict[str, object]:
        return {"private": True, "visibility": "private", "default_branch": "main"}

    monkeypatch.setattr(gateway, "_json", private_json)
    with pytest.raises(ServiceProblem, match="private repositories"):
        asyncio.run(gateway.resolve(RepositoryResolveRequest(repository="acme/private"), settings))


def test_repository_resolution_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(Settings.from_env(), quotas_enabled=False)
    manager = RepositoryManager(settings)
    calls = 0

    async def fake_resolve(
        request: RepositoryResolveRequest, _: Settings,
    ) -> RepositoryResolveResponse:
        nonlocal calls
        calls += 1
        sha = "3" * 40
        return RepositoryResolveResponse(
            repository=RepositoryIdentity(
                owner="acme", name="cached", commit_sha=sha,
                html_url=f"https://github.com/acme/cached/tree/{sha}",
            ),
            requested_ref=request.ref or "main",
            canonical_path=f"/github/acme/cached/commit/{sha}?encoding=o200k_base",
        )

    monkeypatch.setattr(manager.gateway, "resolve", fake_resolve)
    target = RepositoryResolveRequest(repository="acme/cached")
    first = asyncio.run(manager.resolve(target, "127.0.0.1"))
    second = asyncio.run(manager.resolve(target, "127.0.0.1"))
    assert first == second
    assert calls == 1


def test_archive_inventory_deduplicates_skill_resources_and_redacts_mcp() -> None:
    settings = Settings.from_env()
    identity = RepositoryIdentity(
        owner="owner", name="repo", commit_sha="a" * 40,
        html_url="https://github.com/owner/repo/tree/" + "a" * 40,
    )
    result = analyze_archive(archive({
        "skills/demo/SKILL.md": SKILL.encode(),
        "skills/demo/references/guide.md": b"Optional details.",
        "skills/demo/AGENTS.md": b"Owned by the skill.",
        "AGENTS.md": b"Repository instructions.",
        ".claude/rules/tests.md": b"Only for tests.",
        ".mcp.json": b'{"mcpServers":{"local":{"command":"secret-command","args":["secret"]},"remote":{"url":"https://secret"}}}',
    }, {
        "skills/demo/link.md": "../../outside",
        "skills/demo/second-link.md": "../../outside",
    }), identity, "o200k_base", settings)
    by_kind: dict[str, list] = {}
    for item in result.report.inventory:
        by_kind.setdefault(item.kind, []).append(item)
    assert len(by_kind["skill"]) == 1
    assert by_kind["skill"][0].description == "Demonstrate a compact workflow."
    assert len(by_kind["instruction"]) == 1
    assert len(by_kind["rule"]) == 1
    assert {server.name for server in by_kind["mcp_config"][0].mcp_servers} == {"local", "remote"}
    serialized = result.report.model_dump_json()
    assert "secret-command" not in serialized
    assert "https://secret" not in serialized
    assert any(component.path.endswith("guide.md") for component in by_kind["skill"][0].components)
    link_warning = next(warning for warning in result.report.warnings if warning.code == "link_skipped")
    assert link_warning.count == 2


def test_nested_skills_assign_optional_files_to_nearest_skill() -> None:
    settings = Settings.from_env()
    identity = RepositoryIdentity(
        owner="owner", name="nested", commit_sha="e" * 40,
        html_url="https://github.com/owner/nested/tree/" + "e" * 40,
    )
    child = SKILL.replace("name: demo", "name: child")
    result = analyze_archive(archive({
        "skills/parent/SKILL.md": SKILL.encode(),
        "skills/parent/references/parent.md": b"Parent reference.",
        "skills/parent/child/SKILL.md": child.encode(),
        "skills/parent/child/references/child.md": b"Child reference.",
    }), identity, "o200k_base", settings)

    skills = {item.path: item for item in result.report.inventory}
    parent_paths = {component.path for component in skills["skills/parent/SKILL.md"].components}
    child_paths = {component.path for component in skills["skills/parent/child/SKILL.md"].components}
    assert "skills/parent/references/parent.md" in parent_paths
    assert "skills/parent/child/references/child.md" not in parent_paths
    assert "skills/parent/child/references/child.md" in child_paths


def test_manifest_declared_source_agents_are_discovered() -> None:
    settings = Settings.from_env()
    identity = RepositoryIdentity(
        owner="owner", name="agents", commit_sha="6" * 40,
        html_url="https://github.com/owner/agents/tree/" + "6" * 40,
    )
    agent = b"""---
name: Frontend Developer
description: Builds accessible user interfaces.
color: cyan
emoji: screen
vibe: Precise and pragmatic.
---

# Frontend Developer Agent Personality

Build the interface carefully.
"""
    result = analyze_archive(archive({
        "divisions.json": b'{"divisions":{"engineering":{"label":"Engineering"}}}',
        "engineering/frontend-developer.md": agent,
        "strategy/playbook.md": agent,
        "README.md": b"Repository overview.",
    }), identity, "o200k_base", settings)

    assert len(result.report.inventory) == 1
    discovered = result.report.inventory[0]
    assert discovered.kind == "agent"
    assert discovered.path == "engineering/frontend-developer.md"
    assert discovered.name == "Frontend Developer"
    assert discovered.description == "Builds accessible user interfaces."
    assert discovered.harnesses == ["claude_code", "github_copilot"]
    assert discovered.tokens and discovered.tokens > 0


def test_manifest_agent_requires_identity_frontmatter() -> None:
    settings = Settings.from_env()
    identity = RepositoryIdentity(
        owner="owner", name="agents", commit_sha="5" * 40,
        html_url="https://github.com/owner/agents/tree/" + "5" * 40,
    )
    result = analyze_archive(archive({
        "divisions.json": b'{"divisions":{"engineering":{}}}',
        "engineering/README.md": b"# Division overview",
    }), identity, "o200k_base", settings)

    assert result.report.inventory == []
    assert result.report.warnings[0].code == "invalid_agent"


def test_manifest_agent_tolerates_unquoted_colon_in_description() -> None:
    settings = Settings.from_env()
    identity = RepositoryIdentity(
        owner="owner", name="agents", commit_sha="4" * 40,
        html_url="https://github.com/owner/agents/tree/" + "4" * 40,
    )
    result = analyze_archive(archive({
        "divisions.json": b'{"divisions":{"engineering":{}}}',
        "engineering/tooling.md": b"""---
name: Tooling Engineer
description: Builds tools with great DX: fast, obvious, and scriptable.
---

# Tooling Engineer
""",
    }), identity, "o200k_base", settings)

    assert len(result.report.inventory) == 1
    assert result.report.inventory[0].name == "Tooling Engineer"
    assert result.report.inventory[0].description == "Builds tools with great DX: fast, obvious, and scriptable."


def test_repository_relevant_file_limit_is_independent_of_upload_limit() -> None:
    settings = replace(
        Settings.from_env(), max_items=1, repo_max_relevant_files=1100
    )
    identity = RepositoryIdentity(
        owner="owner", name="large", commit_sha="f" * 40,
        html_url="https://github.com/owner/large/tree/" + "f" * 40,
    )
    files = {
        f"skills/skill-{index}/SKILL.md": SKILL.replace(
            "name: demo", f"name: skill-{index}"
        ).encode()
        for index in range(1001)
    }
    result = analyze_archive(archive(files), identity, "o200k_base", settings)
    assert len(result.report.inventory) == 1001


def test_file_backed_archive_analysis_and_stream_limits() -> None:
    settings = replace(
        Settings.from_env(),
        repo_archive_max_bytes=64,
        repo_archive_memory_bytes=8,
    )
    gateway = GitHubGateway(settings)
    response = httpx.Response(
        200,
        content=b"x" * 32,
        request=httpx.Request("GET", "https://codeload.github.com/archive"),
    )
    streamed = asyncio.run(gateway._read_limited(response))
    try:
        assert streamed.read() == b"x" * 32
        assert getattr(streamed, "_rolled", False) is True
    finally:
        streamed.close()

    oversized = httpx.Response(
        200,
        content=b"x" * 65,
        request=httpx.Request("GET", "https://codeload.github.com/archive"),
    )
    with pytest.raises(ServiceProblem) as raised:
        asyncio.run(gateway._read_limited(oversized))
    assert raised.value.code == "archive_too_large"

    identity = RepositoryIdentity(
        owner="owner", name="spooled", commit_sha="8" * 40,
        html_url="https://github.com/owner/spooled/tree/" + "8" * 40,
    )
    payload = archive({"skills/demo/SKILL.md": SKILL.encode()})
    with tempfile.SpooledTemporaryFile(max_size=8, mode="w+b") as file:
        file.write(payload)
        result = analyze_archive(file, identity, "o200k_base", Settings.from_env())
    assert len(result.report.inventory) == 1


def test_repository_tree_preflight_rejects_unsafe_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        Settings.from_env(),
        repo_max_total_bytes=100,
        repo_max_content_bytes=5,
        repo_max_file_bytes=10,
    )
    gateway = GitHubGateway(settings)

    async def truncated(_: str) -> dict[str, object]:
        return {"tree": [], "truncated": True}

    monkeypatch.setattr(gateway, "_json", truncated)
    with pytest.raises(ServiceProblem) as raised:
        asyncio.run(gateway._preflight("owner", "repo", "a" * 40))
    assert raised.value.code == "repository_tree_too_large"

    async def malformed(_: str) -> dict[str, object]:
        return {"tree": ["not-an-entry"], "truncated": False}

    monkeypatch.setattr(gateway, "_json", malformed)
    with pytest.raises(ServiceProblem) as raised:
        asyncio.run(gateway._preflight("owner", "repo", "a" * 40))
    assert raised.value.code == "github_invalid_response"

    async def excessive_relevant(_: str) -> dict[str, object]:
        return {
            "tree": [{
                "path": "skills/demo/SKILL.md", "type": "blob",
                "size": 6, "sha": "b" * 40,
            }],
            "truncated": False,
        }

    monkeypatch.setattr(gateway, "_json", excessive_relevant)
    with pytest.raises(ServiceProblem) as raised:
        asyncio.run(gateway._preflight("owner", "repo", "a" * 40))
    assert raised.value.code == "relevant_content_too_large"


def test_repository_report_api_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = archive({"SKILL.md": SKILL.encode(), "AGENTS.md": b"Rules"})
    calls = 0

    async def fake_archive(owner: str, repository: str, sha: str) -> bytes:
        nonlocal calls
        calls += 1
        return payload

    monkeypatch.setattr(repositories.gateway, "archive", fake_archive)
    sha = "b" * 40
    path = f"/api/v1/repositories/github/acme/fixture/commits/{sha}?encoding=o200k_base"
    first = request("GET", path)
    second = request("GET", path)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["mode"] == "repository"
    assert 0 < first.json()["metadata_tokens"] < first.json()["category_totals"]["all_discovered_text"]
    assert second.json()["cached"] is True
    assert calls == 1
    conditional = request("GET", path, headers={"if-none-match": first.headers["etag"]})
    assert conditional.status_code == 304


def test_repository_badge_uses_repository_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "7" * 40
    resolve_calls = 0
    archive_calls = 0

    async def fake_resolve(request: RepositoryResolveRequest, _: str):
        nonlocal resolve_calls
        resolve_calls += 1
        return type("Resolved", (), {"repository": RepositoryIdentity(
            owner="acme", name="fixture", commit_sha=sha,
            html_url=f"https://github.com/acme/fixture/tree/{sha}",
            subdirectory=request.subdirectory,
        )})()

    payload = archive({"skills/demo/SKILL.md": SKILL.encode()})

    async def fake_archive(owner: str, repository: str, commit: str) -> bytes:
        nonlocal archive_calls
        archive_calls += 1
        return payload

    monkeypatch.setattr(repositories, "resolve", fake_resolve)
    monkeypatch.setattr(repositories.gateway, "archive", fake_archive)
    base = f"/badge/github/acme/fixture.svg?path=skills&ref={sha}"
    total = request("GET", base + "&metric=total")
    metadata = request("GET", base + "&metric=metadata")
    assert total.status_code == metadata.status_code == 200
    assert total.headers["content-type"].startswith("image/svg+xml")
    assert total.headers["x-repository-commit"] == sha
    assert total.headers["x-badge-metric"] == "total"
    assert "total tokens" in total.text
    assert "metadata tokens" in metadata.text
    assert total.text != metadata.text
    assert "unavailable" not in total.text + metadata.text
    assert resolve_calls == 0
    assert archive_calls == 1
    assert "max-age=86400" in total.headers["cache-control"]


def test_branch_badges_share_summaries_by_resolved_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_sha = "1" * 40
    develop_sha = "2" * 40
    heads = {"main": main_sha, "stable": main_sha, "develop": develop_sha}
    archive_calls: list[str] = []

    async def fake_resolve(request: RepositoryResolveRequest, _: str):
        sha = heads[request.ref or "main"]
        return type("Resolved", (), {"repository": RepositoryIdentity(
            owner="branch-test", name="fixture", commit_sha=sha,
            html_url=f"https://github.com/branch-test/fixture/tree/{sha}",
            subdirectory=request.subdirectory,
        )})()

    async def fake_archive(owner: str, repository: str, commit: str) -> bytes:
        archive_calls.append(commit)
        return archive({"skills/demo/SKILL.md": SKILL.encode()})

    monkeypatch.setattr(repositories, "resolve", fake_resolve)
    monkeypatch.setattr(repositories.gateway, "archive", fake_archive)
    base = "/badge/github/branch-test/fixture.svg?metric=total&ref="
    main = request("GET", base + "main")
    stable = request("GET", base + "stable")
    develop = request("GET", base + "develop")

    assert main.headers["x-repository-commit"] == main_sha
    assert stable.headers["x-repository-commit"] == main_sha
    assert develop.headers["x-repository-commit"] == develop_sha
    assert archive_calls == [main_sha, develop_sha]
    assert "max-age=300" in main.headers["cache-control"]


def test_cache_enforces_group_entry_limit() -> None:
    async def exercise() -> None:
        cache = AsyncTTLCache(max_entries=10, max_weight=100, ttl_seconds=60)
        await cache.set("a", 1, group="repo", max_group_entries=2)
        await cache.set("b", 2, group="repo", max_group_entries=2)
        await cache.set("c", 3, group="repo", max_group_entries=2)
        assert await cache.get("a") is None
        assert await cache.get("b") == 2
        assert await cache.get("c") == 3

    asyncio.run(exercise())


def test_subdirectories_share_one_full_repository_cache_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = archive({
        "engineering/demo/SKILL.md": SKILL.encode(),
        "engineering/demo/references/guide.md": b"Engineering guide.",
        "marketing/demo/SKILL.md": SKILL.replace("name: demo", "name: marketing").encode(),
    })
    calls = 0

    async def fake_archive(owner: str, repository: str, sha: str) -> bytes:
        nonlocal calls
        calls += 1
        return payload

    monkeypatch.setattr(repositories.gateway, "archive", fake_archive)
    sha = "9" * 40
    base = f"/api/v1/repositories/github/acme/scoped/commits/{sha}?encoding=o200k_base"
    engineering = request("GET", base + "&path=engineering")
    marketing = request("GET", base + "&path=marketing")
    complete = request("GET", base)

    assert engineering.status_code == marketing.status_code == complete.status_code == 200
    assert calls == 1
    assert engineering.json()["repository"]["subdirectory"] == "engineering"
    assert marketing.json()["repository"]["subdirectory"] == "marketing"
    assert {item["path"] for item in engineering.json()["inventory"]} == {"demo/SKILL.md"}
    assert {item["path"] for item in marketing.json()["inventory"]} == {"demo/SKILL.md"}
    assert len(complete.json()["inventory"]) == 2
    assert marketing.json()["cached"] is True


def test_native_count_is_selection_bounded_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    base = Settings.from_env()
    settings = replace(
        base, turnstile_required=False, anthropic_api_key="not-logged",
        anthropic_models=("claude-test",), native_ip_misses_per_hour=100,
        native_global_misses_per_day=100,
    )
    identity = RepositoryIdentity(
        owner="owner", name="repo", commit_sha="c" * 40,
        html_url="https://github.com/owner/repo/tree/" + "c" * 40,
    )
    analysis = analyze_archive(archive({"SKILL.md": SKILL.encode()}), identity, "o200k_base", settings)

    class FakeRepositories:
        async def get(self, *_: object) -> object:
            return analysis

    manager = NativeCountManager(settings, FakeRepositories())  # type: ignore[arg-type]
    calls = 0

    async def fake_count(provider: str, model: str, texts: list[str], key: str) -> int:
        nonlocal calls
        calls += 1
        assert key == "not-logged"
        assert texts
        return 123

    monkeypatch.setattr(manager, "_provider_count", fake_count)
    component = analysis.report.inventory[0].components[0].id
    native_request = NativeCountRequest(
        provider="anthropic", model="claude-test",
        snapshot=NativeSnapshot(owner="owner", repository="repo", commit_sha="c" * 40),
        item_ids=[component],
    )
    first = asyncio.run(manager.count(native_request, "127.0.0.1"))
    second = asyncio.run(manager.count(native_request, "127.0.0.1"))
    assert first.input_tokens == second.input_tokens == 123
    assert second.cached is True
    assert calls == 1


def test_quota_consumers_are_disabled_for_local_testing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(Settings.from_env(), quotas_enabled=False)
    manager = RepositoryManager(settings)
    native = NativeCountManager(settings, manager)

    async def unexpected_consume(*_: object) -> None:
        raise AssertionError("quota storage should not be touched")

    monkeypatch.setattr(manager.quota, "consume", unexpected_consume)
    monkeypatch.setattr(native.quota, "consume", unexpected_consume)
    asyncio.run(manager._consume_repo_quota("127.0.0.1"))
    asyncio.run(native._consume_quota("127.0.0.1"))
