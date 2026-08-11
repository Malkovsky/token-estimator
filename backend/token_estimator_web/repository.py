"""Public GitHub snapshot resolution, safe archive inspection, and inventory caching."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import PurePosixPath
from typing import Any, Awaitable, BinaryIO, Callable, Iterable, TypeVar
from urllib.parse import quote, unquote, urlencode, urlparse

import httpx
import json5
import yaml

from token_estimator import (
    compact_json, frontmatter_identity, normalize_relative_path, normalize_tool,
    split_frontmatter,
)

from .cache import AsyncTTLCache, QuotaExceeded, SingleFlight, SlidingQuota
from .config import Settings
from .errors import ServiceProblem
from .schemas import (
    InventoryComponent, InventoryItem, McpServerSummary, RepositoryIdentity,
    RepositoryReport, RepositoryResolveRequest, RepositoryResolveResponse,
    ScanStats, ScanWarning,
)
from .service import counter_for, method_info, selected_encoding

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 only
    import tomli as tomllib


_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_TEXT_SUFFIXES = {".json", ".md", ".mdc", ".rst", ".toml", ".txt", ".yaml", ".yml"}
_MCP_TOOLS_SNAPSHOT = "mcp-tools.json"
_MCP_PROBE_FILES = {
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "build.gradle.kts",
}
_EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", ".venv", "node_modules", "vendor", "dist",
    "build", "target", ".cache", "__pycache__",
}
_T = TypeVar("_T")
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _retry_after(value: str | None, default: int = 60) -> int:
    try:
        return max(1, int(value or default))
    except ValueError:
        return default


async def _notify_progress(
    callback: ProgressCallback | None,
    stage: str,
    message: str,
    **details: Any,
) -> None:
    if callback is None:
        return
    try:
        await callback({"stage": stage, "message": message, **details})
    except Exception:
        # Progress delivery is observational and must never fail an analysis.
        pass


def _sparse_checkout_input(paths: Iterable[str]) -> bytes:
    """Build root-anchored, literal no-cone sparse-checkout patterns."""
    patterns: list[str] = []
    for path in sorted(paths):
        escaped = path.replace("\\", "\\\\")
        for character in ("*", "?", "[", "]"):
            escaped = escaped.replace(character, f"\\{character}")
        patterns.append(f"/{escaped}")
    return (("\n".join(patterns) + "\n") if patterns else "").encode()


@dataclass(frozen=True)
class SnapshotAnalysis:
    report: RepositoryReport
    contents: dict[str, str]
    file_bytes: dict[str, int]
    weight: int


@dataclass
class CachedRepository:
    """One discovered repository snapshot with one or more token encodings."""

    analyses: dict[str, SnapshotAnalysis]

    @property
    def weight(self) -> int:
        if not self.analyses:
            return 0
        first = next(iter(self.analyses.values()))
        return sum(first.file_bytes.values()) + sum(
            len(analysis.report.model_dump_json())
            for analysis in self.analyses.values()
        )


@dataclass(frozen=True)
class SnapshotSource:
    archive: BinaryIO
    archive_members: int
    warnings: tuple[ScanWarning, ...] = ()


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    oid: str
    mode: str
    size: int


@dataclass(frozen=True)
class BadgeSummary:
    commit_sha: str
    metadata_tokens: int
    total_tokens: int
    mutable_ref: bool


@dataclass(frozen=True)
class RepositoryLocation:
    owner: str
    repository: str
    ref: str | None = None
    subdirectory: str | None = None


@dataclass(frozen=True)
class CandidatePaths:
    skill_files: list[str]
    optional_paths: dict[PurePosixPath, list[str]]
    selected: set[str]
    mcp_snapshot: str | None
    mcp_probes: set[str]


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:20]
    return f"item_{digest}"


def parse_repository_location(value: str) -> RepositoryLocation:
    raw = value.strip()
    embedded_ref: str | None = None
    embedded_subdirectory: str | None = None
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username:
            raise ServiceProblem(422, "unsupported_repository", "only public HTTPS GitHub repositories are supported")
        if parsed.query or parsed.fragment or parsed.port:
            raise ServiceProblem(422, "invalid_repository", "repository URL must not contain query, fragment, credentials, or port")
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ServiceProblem(422, "invalid_repository", "GitHub repository URL is incomplete")
        tail = parts[2:]
        if tail:
            if len(tail) < 2 or tail[0] != "tree":
                raise ServiceProblem(
                    422, "invalid_repository",
                    "use a GitHub repository URL or a /tree/<ref>/<folder> URL",
                )
            embedded_ref = tail[1]
            embedded_subdirectory = "/".join(tail[2:]) or None
        parts = parts[:2]
    else:
        parts = [part for part in raw.split("/") if part]
    if len(parts) != 2:
        raise ServiceProblem(
            422, "invalid_repository",
            "use owner/repository or a GitHub repository or folder URL",
        )
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repository):
        raise ServiceProblem(422, "invalid_repository", "invalid GitHub owner or repository name")
    return RepositoryLocation(
        owner=owner, repository=repository, ref=embedded_ref,
        subdirectory=embedded_subdirectory,
    )


def parse_repository(value: str) -> tuple[str, str]:
    location = parse_repository_location(value)
    return location.owner, location.repository


def normalize_subdirectory(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return normalize_relative_path(value.strip().strip("/"))
    except ValueError as error:
        raise ServiceProblem(422, "invalid_subdirectory", str(error)) from error


class GitHubGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "agentic-token-estimator/0.2",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if authenticated and self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        return headers

    async def _json(self, path: str) -> dict[str, Any]:
        timeout = httpx.Timeout(
            self.settings.repo_timeout_seconds,
            connect=min(10.0, float(self.settings.repo_timeout_seconds)),
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    f"{self.settings.github_api_base.rstrip('/')}{path}", headers=self._headers()
                )
        except httpx.TimeoutException as error:
            raise ServiceProblem(503, "github_timeout", "GitHub request timed out") from error
        except httpx.HTTPError as error:
            raise ServiceProblem(502, "github_unavailable", "GitHub is unavailable") from error
        if response.status_code == 404:
            raise ServiceProblem(404, "repository_not_found", "repository or ref was not found")
        if response.status_code in {403, 429}:
            retry = _retry_after(response.headers.get("retry-after"))
            raise ServiceProblem(429, "github_rate_limited", "GitHub rate limit reached", retry)
        if response.status_code >= 400:
            raise ServiceProblem(502, "github_error", f"GitHub returned HTTP {response.status_code}")
        try:
            value = response.json()
        except ValueError as error:
            raise ServiceProblem(502, "github_invalid_response", "GitHub returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ServiceProblem(502, "github_invalid_response", "GitHub returned an unexpected document")
        return value

    async def public_metadata(self, owner: str, repository: str) -> dict[str, Any]:
        """Return repository metadata only when GitHub confirms it is public."""
        if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repository):
            raise ServiceProblem(422, "invalid_repository", "invalid GitHub repository")
        metadata = await self._json(f"/repos/{quote(owner)}/{quote(repository)}")
        if metadata.get("private") is True or metadata.get("visibility") not in {
            None, "public",
        }:
            raise ServiceProblem(
                422, "private_repository", "private repositories are not supported"
            )
        return metadata

    async def resolve(self, request: RepositoryResolveRequest, settings: Settings) -> RepositoryResolveResponse:
        location = parse_repository_location(request.repository)
        owner, repository = location.owner, location.repository
        metadata = await self.public_metadata(owner, repository)
        default_branch = metadata.get("default_branch")
        requested_ref = request.ref or location.ref or (
            default_branch if isinstance(default_branch, str) else "HEAD"
        )
        if not requested_ref or len(requested_ref) > 250 or "\x00" in requested_ref:
            raise ServiceProblem(422, "invalid_ref", "invalid Git ref")
        if _SHA.fullmatch(requested_ref):
            sha = requested_ref.lower()
        else:
            commit = await self._json(
                f"/repos/{quote(owner)}/{quote(repository)}/commits/{quote(requested_ref, safe='')}"
            )
            sha = commit.get("sha")
            if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise ServiceProblem(502, "github_invalid_response", "GitHub did not return a full commit SHA")
        canonical_owner = metadata.get("owner", {}).get("login", owner)
        canonical_name = metadata.get("name", repository)
        subdirectory = normalize_subdirectory(request.subdirectory or location.subdirectory)
        encoding = selected_encoding(request.encoding, settings)
        query = {"encoding": encoding}
        if subdirectory:
            query["path"] = subdirectory
        canonical = (
            f"/github/{quote(str(canonical_owner))}/{quote(str(canonical_name))}/commit/{sha}"
            f"?{urlencode(query)}"
        )
        html_url = f"https://github.com/{canonical_owner}/{canonical_name}/tree/{sha}"
        if subdirectory:
            html_url = f"{html_url}/{quote(subdirectory, safe='/')}"
        return RepositoryResolveResponse(
            repository=RepositoryIdentity(
                owner=str(canonical_owner), name=str(canonical_name), commit_sha=sha,
                html_url=html_url,
                subdirectory=subdirectory,
            ),
            requested_ref=requested_ref,
            canonical_path=canonical,
        )

    async def archive(
        self, owner: str, repository: str, sha: str,
        progress: ProgressCallback | None = None,
    ) -> BinaryIO | SnapshotSource:
        """Fetch a bounded snapshot, preferring Git's partial-clone protocol."""
        if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repository) or not _SHA.fullmatch(sha):
            raise ServiceProblem(422, "invalid_snapshot", "invalid repository snapshot")
        mode = self.settings.repo_fetch_mode
        await _notify_progress(
            progress, "tree", "Inspecting the immutable repository tree"
        )
        entries, member_count = await self._preflight(owner, repository, sha.lower())
        if mode == "archive":
            await _notify_progress(
                progress, "download", "Downloading the bounded repository archive"
            )
            return await self._archive_download(
                owner, repository, sha, preflight=False, progress=progress
            )
        try:
            return await self._git_snapshot(
                owner, repository, sha.lower(), entries, member_count, progress
            )
        except ServiceProblem as error:
            if mode == "git" or error.status not in {502, 503}:
                raise
        await _notify_progress(
            progress, "download",
            "Filtered Git was unavailable; downloading the bounded archive",
        )
        return await self._archive_download(
            owner, repository, sha, preflight=False, progress=progress
        )

    async def _run_git(
        self, directory: str, *arguments: str, input_bytes: bytes | None = None,
    ) -> bytes:
        git = shutil.which("git")
        if git is None:
            raise ServiceProblem(502, "git_unavailable", "Git is not installed")
        environment = os.environ.copy()
        environment.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        })
        configuration = (
            "-c", "core.hooksPath=/dev/null",
            "-c", "protocol.file.allow=never",
            "-c", "core.fsmonitor=false",
            "-c", "core.autocrlf=false",
            "-c", "filter.lfs.required=false",
            "-c", "filter.lfs.smudge=",
            "-c", "filter.lfs.clean=",
        )
        try:
            process = await asyncio.create_subprocess_exec(
                git, *configuration, *arguments,
                cwd=directory, env=environment,
                stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise ServiceProblem(502, "git_unavailable", "Git could not be started") from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_bytes), self.settings.repo_timeout_seconds
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            if asyncio.current_task() and asyncio.current_task().cancelling():
                raise
            raise ServiceProblem(503, "git_timeout", "GitHub Git request timed out")
        if process.returncode != 0:
            detail = stderr[-1000:].decode("utf-8", errors="replace").strip()
            if "not our ref" in detail or "couldn't find remote ref" in detail:
                raise ServiceProblem(404, "snapshot_not_found", "repository snapshot was not found")
            raise ServiceProblem(502, "git_fetch_failed", "GitHub Git request failed")
        return stdout

    async def _git_snapshot(
        self, owner: str, repository: str, sha: str,
        entries: dict[str, GitTreeEntry], member_count: int,
        progress: ProgressCallback | None = None,
    ) -> SnapshotSource:
        directory = tempfile.mkdtemp(prefix="token-estimator-git-")
        output: BinaryIO | None = None
        try:
            await self._run_git(directory, "init", "--quiet")
            await self._run_git(
                directory, "remote", "add", "origin",
                f"https://github.com/{owner}/{repository}.git",
            )
            await _notify_progress(
                progress, "fetch", "Fetching the immutable commit with filtered Git"
            )
            await self._run_git(
                directory, "fetch", "--quiet", "--filter=blob:none", "--no-tags",
                "--depth=1", "origin", sha,
            )
            candidates = _static_candidate_paths(entries)
            warnings: list[ScanWarning] = []
            selected = {
                path for path in candidates.selected
                if entries[path].size <= self.settings.repo_max_file_bytes
            }
            for path in sorted(candidates.selected - selected):
                warnings.append(ScanWarning(
                    code="file_too_large", message="Skipped oversized text file", path=path
                ))

            manifest = entries.get("divisions.json")
            manifest_limit = min(self.settings.repo_max_file_bytes, 256 * 1024)
            checkout_paths = set(selected)
            if manifest is not None and manifest.size <= manifest_limit:
                checkout_paths.add("divisions.json")

            if checkout_paths:
                expected_bytes = sum(entries[path].size for path in selected)
                await _notify_progress(
                    progress, "download", "Downloading relevant agentic-harness files",
                    files=len(selected), total_bytes=expected_bytes,
                )
                await self._run_git(directory, "sparse-checkout", "init", "--no-cone")
                await self._run_git(
                    directory, "sparse-checkout", "set", "--no-cone", "--stdin",
                    input_bytes=_sparse_checkout_input(checkout_paths),
                )
                await self._run_git(
                    directory, "checkout", "--quiet", "--force", "--detach", "FETCH_HEAD"
                )
                await _notify_progress(
                    progress, "download", "Relevant files downloaded",
                    files=len(selected), loaded_bytes=expected_bytes,
                    total_bytes=expected_bytes,
                )
            else:
                await _notify_progress(
                    progress, "download", "No matching harness files require downloading",
                    files=0, loaded_bytes=0, total_bytes=0,
                )

            if manifest is not None and "divisions.json" in checkout_paths:
                try:
                    with open(os.path.join(directory, "divisions.json"), "rb") as handle:
                        manifest_data = handle.read(manifest_limit + 1)
                except OSError:
                    manifest_data = b""
                roots = _catalog_roots(manifest_data)
                catalog = {
                    path for path, entry in entries.items()
                    if PurePosixPath(path).suffix.lower() == ".md"
                    and len(PurePosixPath(path).parts) > 1
                    and PurePosixPath(path).parts[0] in roots
                    and entry.size <= self.settings.repo_max_file_bytes
                }
                oversized_catalog = {
                    path for path, entry in entries.items()
                    if PurePosixPath(path).suffix.lower() == ".md"
                    and len(PurePosixPath(path).parts) > 1
                    and PurePosixPath(path).parts[0] in roots
                    and entry.size > self.settings.repo_max_file_bytes
                }
                for path in sorted(oversized_catalog):
                    warnings.append(ScanWarning(
                        code="file_too_large", message="Skipped oversized text file", path=path
                    ))
                selected.update(catalog)
                checkout_paths.update(catalog)
                if len(selected) > self.settings.repo_max_relevant_files:
                    raise ServiceProblem(
                        413, "too_many_relevant_files",
                        "repository contains too many relevant files",
                    )
                if catalog:
                    await _notify_progress(
                        progress, "download", "Downloading manifest-declared agent files",
                        files=len(selected),
                        total_bytes=sum(entries[path].size for path in selected),
                    )
                    await self._run_git(
                        directory, "sparse-checkout", "set", "--no-cone", "--stdin",
                        input_bytes=_sparse_checkout_input(checkout_paths),
                    )
                    await _notify_progress(
                        progress, "download", "Relevant files downloaded",
                        files=len(selected),
                        loaded_bytes=sum(entries[path].size for path in selected),
                        total_bytes=sum(entries[path].size for path in selected),
                    )

            relevant_bytes = sum(entries[path].size for path in selected)
            if relevant_bytes > self.settings.repo_max_content_bytes:
                raise ServiceProblem(
                    413, "relevant_content_too_large",
                    "relevant repository text exceeds the configured limit",
                )

            ordered = sorted(selected)
            regular = [path for path in ordered if entries[path].mode != "120000"]
            raw_blobs = b""
            if regular:
                raw_blobs = await self._run_git(
                    directory, "cat-file", "--batch",
                    input_bytes=("\n".join(entries[path].oid for path in regular) + "\n").encode(),
                )
            blob_values = _parse_cat_file_batch(raw_blobs, regular, entries)
            output = tempfile.SpooledTemporaryFile(
                max_size=self.settings.repo_archive_memory_bytes, mode="w+b"
            )
            with tarfile.open(fileobj=output, mode="w") as bundle:
                for path in ordered:
                    entry = entries[path]
                    info = tarfile.TarInfo(f"snapshot/{path}")
                    info.mode = int(entry.mode[-3:], 8)
                    if entry.mode == "120000":
                        info.type = tarfile.SYMTYPE
                        info.linkname = ""
                        bundle.addfile(info)
                        continue
                    data = blob_values[path]
                    info.size = len(data)
                    bundle.addfile(info, io.BytesIO(data))
                if manifest is not None and "divisions.json" in checkout_paths:
                    with open(os.path.join(directory, "divisions.json"), "rb") as handle:
                        data = handle.read(manifest_limit + 1)
                    info = tarfile.TarInfo("snapshot/divisions.json")
                    info.size = len(data)
                    bundle.addfile(info, io.BytesIO(data))
            output.seek(0)
            return SnapshotSource(output, member_count, tuple(warnings))
        except BaseException:
            if output is not None:
                output.close()
            raise
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    async def _archive_download(
        self, owner: str, repository: str, sha: str, *, preflight: bool = True,
        progress: ProgressCallback | None = None,
    ) -> BinaryIO:
        if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repository) or not _SHA.fullmatch(sha):
            raise ServiceProblem(422, "invalid_snapshot", "invalid repository snapshot")
        if preflight:
            await self._preflight(owner, repository, sha)
        url = (
            f"{self.settings.github_api_base.rstrip('/')}/repos/{quote(owner)}/"
            f"{quote(repository)}/tarball/{sha.lower()}"
        )
        timeout = httpx.Timeout(
            self.settings.repo_timeout_seconds,
            connect=min(10.0, float(self.settings.repo_timeout_seconds)),
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", url, headers=self._headers(), follow_redirects=False) as response:
                    if response.status_code == 404:
                        raise ServiceProblem(404, "snapshot_not_found", "repository snapshot was not found")
                    if response.status_code in {403, 429}:
                        retry = _retry_after(response.headers.get("retry-after"))
                        raise ServiceProblem(429, "github_rate_limited", "GitHub rate limit reached", retry)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        parsed = urlparse(location)
                        if parsed.scheme != "https" or parsed.hostname != "codeload.github.com":
                            raise ServiceProblem(502, "unsafe_archive_redirect", "GitHub returned an unexpected archive host")
                    elif response.status_code == 200:
                        location = ""
                        return await self._read_limited(response, progress)
                    else:
                        raise ServiceProblem(502, "github_error", f"GitHub archive returned HTTP {response.status_code}")
                async with client.stream("GET", location, headers=self._headers(False), follow_redirects=False) as archive_response:
                    if archive_response.status_code != 200:
                        raise ServiceProblem(502, "github_error", f"GitHub archive returned HTTP {archive_response.status_code}")
                    return await self._read_limited(archive_response, progress)
        except ServiceProblem:
            raise
        except httpx.TimeoutException as error:
            raise ServiceProblem(503, "github_timeout", "GitHub archive request timed out") from error
        except httpx.HTTPError as error:
            raise ServiceProblem(502, "github_unavailable", "GitHub archive is unavailable") from error

    async def _preflight(
        self, owner: str, repository: str, sha: str,
    ) -> tuple[dict[str, GitTreeEntry], int]:
        document = await self._json(
            f"/repos/{quote(owner)}/{quote(repository)}/git/trees/"
            f"{quote(sha.lower())}?recursive=1"
        )
        entries = document.get("tree")
        if not isinstance(entries, list):
            raise ServiceProblem(
                502, "github_invalid_response", "GitHub returned an invalid repository tree"
            )
        if document.get("truncated") is True:
            raise ServiceProblem(
                413, "repository_tree_too_large",
                "repository tree exceeds GitHub's recursive listing limit",
            )
        if len(entries) + 1 > self.settings.repo_max_members:
            raise ServiceProblem(
                413, "too_many_archive_members", "repository archive has too many members"
            )

        total_bytes = 0
        paths: dict[str, int] = {}
        blobs: dict[str, GitTreeEntry] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceProblem(
                    502, "github_invalid_response",
                    "GitHub returned an invalid repository tree entry",
                )
            if entry.get("type") != "blob":
                continue
            path = entry.get("path")
            size = entry.get("size")
            if not isinstance(path, str) or not isinstance(size, int) or size < 0:
                raise ServiceProblem(
                    502, "github_invalid_response",
                    "GitHub returned an invalid repository tree entry",
                )
            total_bytes += size
            if total_bytes > self.settings.repo_max_total_bytes:
                raise ServiceProblem(
                    413, "repository_too_large",
                    "repository contents exceed the configured preflight limit",
                )
            try:
                normalized = normalize_relative_path(path)
            except ValueError:
                continue
            if (
                "\n" not in normalized and "\r" not in normalized
                and not _EXCLUDED_PARTS.intersection(PurePosixPath(normalized).parts)
            ):
                oid = entry.get("sha")
                mode = entry.get("mode", "100644")
                if not isinstance(oid, str) or not _SHA.fullmatch(oid):
                    raise ServiceProblem(
                        502, "github_invalid_response",
                        "GitHub returned an invalid repository tree entry",
                    )
                if not isinstance(mode, str) or not re.fullmatch(r"[0-7]{6}", mode):
                    raise ServiceProblem(
                        502, "github_invalid_response",
                        "GitHub returned an invalid repository tree entry",
                    )
                paths[normalized] = size
                blobs[normalized] = GitTreeEntry(normalized, oid, mode, size)

        candidates = _static_candidate_paths(paths)
        if len(candidates.selected) > self.settings.repo_max_relevant_files:
            raise ServiceProblem(
                413, "too_many_relevant_files", "repository contains too many relevant files"
            )
        candidate_bytes = sum(
            paths[path]
            for path in candidates.selected
            if paths[path] <= self.settings.repo_max_file_bytes
        )
        if candidate_bytes > self.settings.repo_max_content_bytes:
            raise ServiceProblem(
                413, "relevant_content_too_large",
                "relevant repository text exceeds the configured limit",
            )
        return blobs, len(entries) + 1

    async def _read_limited(
        self, response: httpx.Response, progress: ProgressCallback | None = None,
    ) -> BinaryIO:
        raw_length = response.headers.get("content-length")
        expected: int | None = None
        if raw_length:
            try:
                expected = int(raw_length)
                if expected > self.settings.repo_archive_max_bytes:
                    raise ServiceProblem(
                        413, "archive_too_large",
                        "repository archive exceeds the configured limit",
                    )
            except ValueError:
                pass
        output = tempfile.SpooledTemporaryFile(
            max_size=self.settings.repo_archive_memory_bytes, mode="w+b"
        )
        total = 0
        reported = 0
        try:
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self.settings.repo_archive_max_bytes:
                    raise ServiceProblem(
                        413, "archive_too_large",
                        "repository archive exceeds the configured limit",
                    )
                output.write(chunk)
                if total - reported >= 512 * 1024 or (expected is not None and total >= expected):
                    reported = total
                    await _notify_progress(
                        progress, "download", "Downloading the bounded repository archive",
                        loaded_bytes=total, total_bytes=expected,
                    )
            output.seek(0)
            return output
        except BaseException:
            output.close()
            raise


def _detection(path: str) -> tuple[str, list[str], str] | None:
    lower = path.lower()
    name = PurePosixPath(path).name
    if lower.endswith("/.codex/config.toml") or lower == ".codex/config.toml":
        return "mcp_config", ["codex"], "configuration_only"
    if lower in {".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json", ".gemini/settings.json"} or lower.endswith("/.mcp.json"):
        harness = "claude_code"
        if ".cursor/" in lower:
            harness = "cursor"
        elif ".vscode/" in lower:
            harness = "github_copilot"
        elif ".gemini/" in lower:
            harness = "gemini_cli"
        return "mcp_config", [harness], "configuration_only"
    if name in {"AGENTS.md", "AGENTS.override.md"}:
        return "instruction", ["codex", "cursor", "github_copilot"], "hierarchical"
    if name in {"CLAUDE.md", "CLAUDE.local.md"}:
        return "instruction", ["claude_code"], "hierarchical"
    if name == "GEMINI.md":
        return "instruction", ["gemini_cli"], "hierarchical"
    if lower == ".github/copilot-instructions.md":
        return "instruction", ["github_copilot"], "hierarchical"
    if "/.claude/rules/" in f"/{lower}" and lower.endswith(".md"):
        return "rule", ["claude_code"], "conditional"
    if "/.cursor/rules/" in f"/{lower}" and lower.endswith(".mdc"):
        return "rule", ["cursor"], "conditional"
    if lower == ".cursorrules" or lower.endswith("/.cursorrules"):
        return "rule", ["cursor"], "hierarchical"
    if "/.github/instructions/" in f"/{lower}" and lower.endswith(".instructions.md"):
        return "rule", ["github_copilot"], "conditional"
    if "/.github/agents/" in f"/{lower}" and lower.endswith(".agent.md"):
        return "agent", ["github_copilot"], "on_demand"
    if "/.claude/agents/" in f"/{lower}" and lower.endswith(".md"):
        return "agent", ["claude_code"], "on_demand"
    if "/.github/prompts/" in f"/{lower}" and lower.endswith(".prompt.md"):
        return "prompt", ["github_copilot"], "on_demand"
    if "/.claude/commands/" in f"/{lower}" and lower.endswith(".md"):
        return "prompt", ["claude_code"], "on_demand"
    if "/.cursor/commands/" in f"/{lower}" and lower.endswith(".md"):
        return "prompt", ["cursor"], "on_demand"
    if "/.gemini/commands/" in f"/{lower}" and lower.endswith(".toml"):
        return "prompt", ["gemini_cli"], "on_demand"
    return None


def _skill_harnesses(path: str) -> list[str]:
    lower = f"/{path.lower()}"
    if "/.agents/skills/" in lower:
        return ["codex", "gemini_cli"]
    if "/.claude/skills/" in lower:
        return ["claude_code"]
    if "/.gemini/skills/" in lower:
        return ["gemini_cli"]
    return ["generic"]


def _nearest_skill_root(
    path: PurePosixPath, skill_roots: set[PurePosixPath]
) -> PurePosixPath | None:
    """Return the deepest skill directory containing path in O(path depth)."""
    current = path.parent
    while True:
        if current in skill_roots:
            return current
        if current == PurePosixPath("."):
            return None
        current = current.parent


def _is_optional_skill_text(path: PurePosixPath, root: PurePosixPath) -> bool:
    relative = path if root == PurePosixPath(".") else path.relative_to(root)
    excluded = {part.lower() for part in relative.parts}.intersection(
        {"agents", "assets", "scripts"}
    )
    return relative.suffix.lower() in _TEXT_SUFFIXES and not excluded


def _static_candidate_paths(paths: Iterable[str]) -> CandidatePaths:
    """Select candidates using paths alone, before any repository content is read."""
    available = list(paths)
    available_set = set(available)
    skill_files = sorted(
        path for path in available if PurePosixPath(path).name == "SKILL.md"
    )
    skill_roots = {PurePosixPath(path).parent for path in skill_files}
    optional_paths: dict[PurePosixPath, list[str]] = {
        root: [] for root in skill_roots
    }
    mcp_snapshot = _MCP_TOOLS_SNAPSHOT if _MCP_TOOLS_SNAPSHOT in available_set else None
    mcp_probes = _MCP_PROBE_FILES.intersection(available_set)
    selected: set[str] = set(skill_files) | mcp_probes
    if mcp_snapshot:
        selected.add(mcp_snapshot)
    for path in available:
        pure = PurePosixPath(path)
        if _detection(path):
            selected.add(path)
        if pure.name == "SKILL.md" or path == _MCP_TOOLS_SNAPSHOT:
            continue
        root = _nearest_skill_root(pure, skill_roots)
        if root is not None and _is_optional_skill_text(pure, root):
            selected.add(path)
            optional_paths[root].append(path)
    return CandidatePaths(
        skill_files=skill_files,
        optional_paths=optional_paths,
        selected=selected,
        mcp_snapshot=mcp_snapshot,
        mcp_probes=mcp_probes,
    )


def _frontmatter_metadata(content: str) -> tuple[str | None, str | None]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return None, None
    try:
        loaded = yaml.safe_load("\n".join(lines[1:closing])) or {}
        metadata = loaded if isinstance(loaded, dict) else {}
    except yaml.YAMLError:
        metadata = {}

    # Some public catalogs contain otherwise valid one-line metadata with an
    # unquoted colon. Preserve discovery with a deliberately narrow fallback.
    if not metadata:
        for line in lines[1:closing]:
            if line[:1].isspace() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key not in {"name", "title", "description"} or not value.strip():
                continue
            cleaned = value.strip()
            if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
                cleaned = cleaned[1:-1]
            metadata[key] = cleaned
    raw_name = metadata.get("name", metadata.get("title"))
    raw_description = metadata.get("description")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    description = (
        raw_description.strip()
        if isinstance(raw_description, str) and raw_description.strip()
        else None
    )
    return name, description


def _catalog_roots(data: bytes) -> set[str]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if not isinstance(document, dict) or not isinstance(document.get("divisions"), dict):
        return set()
    return {
        name
        for name, metadata in document["divisions"].items()
        if isinstance(name, str) and _NAME.fullmatch(name) and isinstance(metadata, dict)
    }


def _catalog_agent_paths(
    tar: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    max_file_bytes: int,
) -> set[str]:
    """Find source agents declared by a bounded top-level divisions manifest."""
    manifest = members.get("divisions.json")
    manifest_limit = min(max_file_bytes, 256 * 1024)
    if manifest is None or manifest.size > manifest_limit:
        return set()
    handle = tar.extractfile(manifest)
    if handle is None:
        return set()
    roots = _catalog_roots(handle.read(manifest_limit + 1))
    return {
        path
        for path in members
        if PurePosixPath(path).suffix.lower() == ".md"
        and len(PurePosixPath(path).parts) > 1
        and PurePosixPath(path).parts[0] in roots
    }


def _parse_cat_file_batch(
    document: bytes,
    paths: list[str],
    entries: dict[str, GitTreeEntry],
) -> dict[str, bytes]:
    """Decode the length-framed output of one local `git cat-file --batch`."""
    values: dict[str, bytes] = {}
    offset = 0
    for path in paths:
        line_end = document.find(b"\n", offset)
        if line_end < 0:
            raise ServiceProblem(502, "git_invalid_response", "Git returned an incomplete blob batch")
        header = document[offset:line_end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise ServiceProblem(502, "git_invalid_response", "Git returned an invalid blob batch")
        try:
            size = int(header[2])
        except ValueError as error:
            raise ServiceProblem(502, "git_invalid_response", "Git returned an invalid blob size") from error
        start = line_end + 1
        end = start + size
        if size != entries[path].size or end >= len(document) or document[end:end + 1] != b"\n":
            raise ServiceProblem(502, "git_invalid_response", "Git returned an invalid blob body")
        values[path] = document[start:end]
        offset = end + 1
    if offset != len(document):
        raise ServiceProblem(502, "git_invalid_response", "Git returned unexpected blob data")
    return values


def _mcp_servers(path: str, content: str) -> list[McpServerSummary]:
    try:
        if path.lower().endswith(".toml"):
            document = tomllib.loads(content)
            servers = document.get("mcp_servers", {})
        else:
            document = json5.loads(content)
            servers = document.get("mcpServers", document.get("servers", {}))
    except Exception:
        return []
    if not isinstance(servers, dict):
        return []
    result: list[McpServerSummary] = []
    for name, config in sorted(servers.items()):
        if not isinstance(name, str) or not isinstance(config, dict):
            continue
        if isinstance(config.get("command"), str):
            transport = "stdio"
        elif config.get("type") == "sse":
            transport = "sse"
        elif isinstance(config.get("url"), str):
            transport = "http"
        else:
            transport = "unknown"
        result.append(McpServerSummary(name=name, transport=transport))
    return result


def _python_requirement_name(value: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", value)
    return match.group(1).lower().replace("_", "-").replace(".", "-") if match else ""


def _string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)


def _mcp_implementation_detected(probes: dict[str, str]) -> bool:
    """Recognize explicit MCP SDK dependencies without inspecting executable source."""
    for path, content in probes.items():
        try:
            if path == "pyproject.toml":
                document = tomllib.loads(content)
                project = document.get("project", {})
                dependencies: list[str] = []
                if isinstance(project, dict):
                    dependencies.extend(
                        item for item in project.get("dependencies", []) if isinstance(item, str)
                    )
                    optional = project.get("optional-dependencies", {})
                    if isinstance(optional, dict):
                        dependencies.extend(_string_values(optional))
                dependency_groups = document.get("dependency-groups", {})
                if isinstance(dependency_groups, dict):
                    for group in dependency_groups.values():
                        if isinstance(group, list):
                            dependencies.extend(
                                item for item in group if isinstance(item, str)
                            )
                if any(
                    _python_requirement_name(item) in {"mcp", "fastmcp"}
                    for item in dependencies
                ):
                    return True
            elif path == "package.json":
                document = json.loads(content)
                if isinstance(document, dict):
                    names: set[str] = set()
                    for key in ("dependencies", "devDependencies", "peerDependencies"):
                        values = document.get(key, {})
                        if isinstance(values, dict):
                            names.update(str(name).lower() for name in values)
                    if names.intersection({"@modelcontextprotocol/sdk", "fastmcp"}):
                        return True
            elif path == "Cargo.toml":
                if re.search(r"(?mi)^\s*(?:rmcp|rust-mcp-sdk)\s*=", content):
                    return True
            elif path == "go.mod":
                if re.search(
                    r"(?mi)^\s*(?:github\.com/mark3labs/mcp-go|github\.com/modelcontextprotocol/go-sdk)\b",
                    content,
                ):
                    return True
            elif path in {"pom.xml", "build.gradle", "build.gradle.kts"}:
                if "io.modelcontextprotocol" in content:
                    return True
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError):
            continue
    return False


def _mcp_snapshot_inventory(
    content: str, counter: Any, settings: Settings,
) -> tuple[list[InventoryItem], dict[str, str]]:
    """Parse the repository's explicit, non-standard tools/list snapshot convention."""
    document = json.loads(content)
    if not isinstance(document, dict):
        raise ValueError("snapshot must be a JSON object")
    if document.get("format") != "mcp-tools-snapshot":
        raise ValueError("format must be 'mcp-tools-snapshot'")
    if document.get("formatVersion") != 1:
        raise ValueError("formatVersion must be 1")
    server = document.get("server")
    if (
        not isinstance(server, dict)
        or not isinstance(server.get("name"), str)
        or not server["name"].strip()
    ):
        raise ValueError("server.name must be a non-empty string")
    tools = document.get("tools")
    if not isinstance(tools, list):
        raise ValueError("tools must be an array")
    if len(tools) > settings.max_tools:
        raise ValueError(f"tools exceeds the configured limit of {settings.max_tools}")

    inventory: list[InventoryItem] = []
    contents: dict[str, str] = {}
    names: set[str] = set()
    server_name = server["name"].strip()
    for raw_tool in tools:
        if not isinstance(raw_tool, dict):
            raise ValueError("every tool must be a JSON object")
        if not isinstance(raw_tool.get("inputSchema"), dict):
            raise ValueError("every tool must contain an object inputSchema")
        name, description, schema = normalize_tool(raw_tool)
        if name in names:
            raise ValueError(f"duplicate tool name {name!r}")
        names.add(name)
        definition = compact_json({
            "name": name, "description": description, "inputSchema": schema,
        })
        item_path = f"{_MCP_TOOLS_SNAPSHOT}#{name}"
        component_id = _stable_id(_MCP_TOOLS_SNAPSHOT, "mcp_tool", name, "definition")
        component = InventoryComponent(
            id=component_id, path=item_path, role="definition", load_policy="discovery",
            characters=len(definition), tokens=counter(definition),
        )
        contents[component_id] = definition
        inventory.append(InventoryItem(
            id=_stable_id(_MCP_TOOLS_SNAPSHOT, "mcp_tool", name),
            path=item_path, kind="mcp_tool", name=name,
            description=description or None, harnesses=["mcp"],
            load_policy="discovery", characters=component.characters,
            tokens=component.tokens, components=[component],
            accounting_note=(
                f"Generated tools/list snapshot for {server_name}; runtime tool results "
                "and server instructions are excluded."
            ),
        ))
    return inventory, contents


def _aggregate_warnings(warnings: list[ScanWarning]) -> list[ScanWarning]:
    grouped: dict[tuple[str, str], ScanWarning] = {}
    for warning in warnings:
        key = (warning.code, warning.message)
        current = grouped.get(key)
        if current is None:
            grouped[key] = warning
        else:
            grouped[key] = current.model_copy(
                update={"count": current.count + warning.count}
            )
    return list(grouped.values())


def _metadata_token_total(
    inventory: list[InventoryItem], counter: Any,
) -> int:
    """Count discovery identity without double-counting skill metadata."""
    total = 0
    for item in inventory:
        explicit = [component.tokens for component in item.components if component.role == "metadata"]
        if explicit:
            total += sum(explicit)
        else:
            total += counter(item.name or "") + counter(item.description or "")
    return total


def _relative_to_scope(path: str, root: str) -> str | None:
    if path == root:
        return PurePosixPath(path).name
    prefix = root + "/"
    return path[len(prefix):] if path.startswith(prefix) else None


def scope_analysis(
    analysis: SnapshotAnalysis,
    subdirectory: str | None,
    cached: bool,
) -> SnapshotAnalysis:
    if subdirectory is None:
        return SnapshotAnalysis(
            report=analysis.report.model_copy(update={"cached": cached}),
            contents=analysis.contents,
            file_bytes=analysis.file_bytes,
            weight=analysis.weight,
        )

    inventory: list[InventoryItem] = []
    component_ids: set[str] = set()
    for item in analysis.report.inventory:
        relative_path = _relative_to_scope(item.path, subdirectory)
        if relative_path is None:
            continue
        components = []
        for component in item.components:
            relative_component = _relative_to_scope(component.path, subdirectory)
            if relative_component is None:
                continue
            components.append(component.model_copy(update={"path": relative_component}))
            component_ids.add(component.id)
        inventory.append(item.model_copy(update={
            "path": relative_path,
            "components": components,
        }))

    totals: dict[str, int] = {}
    for item in inventory:
        if item.tokens is not None:
            totals[item.kind] = totals.get(item.kind, 0) + item.tokens
    totals["all_discovered_text"] = sum(totals.values())
    scoped_file_bytes = {
        path: size for path, size in analysis.file_bytes.items()
        if _relative_to_scope(path, subdirectory) is not None
    }
    repository = analysis.report.repository.model_copy(update={
        "subdirectory": subdirectory,
        "html_url": (
            f"https://github.com/{analysis.report.repository.owner}/"
            f"{analysis.report.repository.name}/tree/"
            f"{analysis.report.repository.commit_sha}/{quote(subdirectory, safe='/')}"
        ),
    })
    report = analysis.report.model_copy(update={
        "repository": repository,
        "inventory": inventory,
        "metadata_tokens": _metadata_token_total(
            inventory, counter_for(analysis.report.method.encoding)
        ),
        "category_totals": totals,
        "scan": analysis.report.scan.model_copy(update={
            "relevant_files": len(scoped_file_bytes),
            "relevant_bytes": sum(scoped_file_bytes.values()),
        }),
        "cached": cached,
    })
    contents = {
        component_id: content for component_id, content in analysis.contents.items()
        if component_id in component_ids
    }
    return SnapshotAnalysis(
        report=report, contents=contents, file_bytes=scoped_file_bytes,
        weight=sum(scoped_file_bytes.values()) + len(report.model_dump_json()),
    )


def analyze_archive(
    archive: bytes | BinaryIO,
    identity: RepositoryIdentity,
    encoding: str,
    settings: Settings,
    *,
    archive_member_count: int | None = None,
    initial_warnings: Iterable[ScanWarning] = (),
) -> SnapshotAnalysis:
    counter = counter_for(encoding)
    warnings = list(initial_warnings)
    archive_file: BinaryIO
    if isinstance(archive, bytes):
        archive_file = io.BytesIO(archive)
    else:
        archive.seek(0)
        archive_file = archive
    try:
        tar = tarfile.open(fileobj=archive_file, mode="r:*")
    except tarfile.TarError as error:
        raise ServiceProblem(502, "invalid_archive", "GitHub returned an invalid tar archive") from error
    with tar:
        members = tar.getmembers()
        if len(members) > settings.repo_max_members:
            raise ServiceProblem(413, "too_many_archive_members", "repository archive has too many members")
        names = [member.name.strip("/") for member in members if member.name.strip("/")]
        first_parts = {name.split("/", 1)[0] for name in names}
        prefix = next(iter(first_parts)) if len(first_parts) == 1 else None
        normalized_members: dict[str, tarfile.TarInfo] = {}
        for member in members:
            raw = member.name.strip("/")
            if prefix and (raw == prefix or raw.startswith(prefix + "/")):
                raw = raw[len(prefix):].lstrip("/")
            if not raw or member.isdir():
                continue
            try:
                path = normalize_relative_path(raw)
            except ValueError:
                warnings.append(ScanWarning(code="unsafe_path", message="Skipped unsafe archive path"))
                continue
            if identity.subdirectory:
                root = identity.subdirectory
                if path == root:
                    continue
                if not path.startswith(root + "/"):
                    continue
                path = path[len(root) + 1:]
            if _EXCLUDED_PARTS.intersection(PurePosixPath(path).parts):
                continue
            if member.issym() or member.islnk():
                warnings.append(ScanWarning(code="link_skipped", message="Skipped archive link", path=path))
                continue
            if not member.isfile() or path in normalized_members:
                continue
            normalized_members[path] = member

        candidates = _static_candidate_paths(normalized_members)
        skill_files = candidates.skill_files
        catalog_agent_paths = _catalog_agent_paths(
            tar, normalized_members, settings.repo_max_file_bytes
        )
        optional_paths = candidates.optional_paths
        selected = set(candidates.selected)
        selected.update(catalog_agent_paths)
        if len(selected) > settings.repo_max_relevant_files:
            raise ServiceProblem(413, "too_many_relevant_files", "repository contains too many relevant files")

        texts: dict[str, str] = {}
        file_bytes: dict[str, int] = {}
        total_bytes = 0
        for path in sorted(selected):
            member = normalized_members[path]
            if member.size > settings.repo_max_file_bytes:
                warnings.append(ScanWarning(code="file_too_large", message="Skipped oversized text file", path=path))
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            data = handle.read(settings.repo_max_file_bytes + 1)
            if len(data) > settings.repo_max_file_bytes:
                warnings.append(ScanWarning(code="file_too_large", message="Skipped oversized text file", path=path))
                continue
            total_bytes += len(data)
            if total_bytes > settings.repo_max_content_bytes:
                raise ServiceProblem(413, "relevant_content_too_large", "relevant repository text exceeds the configured limit")
            if b"\x00" in data:
                warnings.append(ScanWarning(code="binary_skipped", message="Skipped binary file", path=path))
                continue
            try:
                texts[path] = data.decode("utf-8", errors="strict")
                file_bytes[path] = len(data)
            except UnicodeDecodeError:
                warnings.append(ScanWarning(code="invalid_utf8", message="Skipped non-UTF-8 file", path=path))

    probe_contents = {
        path: texts[path] for path in candidates.mcp_probes if path in texts
    }
    mcp_implementation = _mcp_implementation_detected(probe_contents)
    skill_optional_paths = {
        path for paths in candidates.optional_paths.values() for path in paths
    }
    for path in candidates.mcp_probes - skill_optional_paths:
        texts.pop(path, None)
        size = file_bytes.pop(path, 0)
        total_bytes -= size

    inventory: list[InventoryItem] = []
    contents: dict[str, str] = {}
    owned: set[str] = set()
    mcp_snapshot_valid = False
    if candidates.mcp_snapshot and candidates.mcp_snapshot in texts:
        try:
            snapshot_inventory, snapshot_contents = _mcp_snapshot_inventory(
                texts[candidates.mcp_snapshot], counter, settings
            )
            inventory.extend(snapshot_inventory)
            contents.update(snapshot_contents)
            mcp_snapshot_valid = True
        except (json.JSONDecodeError, ValueError) as error:
            warnings.append(ScanWarning(
                code="invalid_mcp_tools_snapshot",
                message=f"Skipped invalid mcp-tools.json: {error}",
                path=candidates.mcp_snapshot,
            ))
    if mcp_implementation and not mcp_snapshot_valid:
        warnings.append(ScanWarning(
            code="mcp_tools_unavailable",
            message=(
                "This repository appears to implement an MCP server, but no valid "
                "mcp-tools.json snapshot was found. The estimator does not execute "
                "repository code, so it cannot call tools/list or include MCP tool "
                "definitions in the token total. Generate and commit mcp-tools.json "
                "to include them."
            ),
        ))
    for skill_path in skill_files:
        content = texts.get(skill_path)
        if content is None:
            continue
        root = PurePosixPath(skill_path).parent
        try:
            frontmatter, body = split_frontmatter(content, skill_path)
            name, description = frontmatter_identity(frontmatter, skill_path)
        except ValueError as error:
            warnings.append(ScanWarning(code="invalid_skill", message=str(error), path=skill_path))
            continue
        components: list[InventoryComponent] = []
        metadata_text = f"{name}\n{description}"
        metadata_id = _stable_id(skill_path, "metadata")
        components.append(InventoryComponent(
            id=metadata_id, path=skill_path, role="metadata", load_policy="discovery",
            characters=len(metadata_text), tokens=counter(name) + counter(description),
        ))
        contents[metadata_id] = metadata_text
        body_id = _stable_id(skill_path, "body")
        components.append(InventoryComponent(
            id=body_id, path=skill_path, role="body", load_policy="activation",
            characters=len(body), tokens=counter(body),
        ))
        contents[body_id] = body
        owned.add(skill_path)
        for path in sorted(optional_paths[root]):
            optional_content = texts.get(path)
            if optional_content is None:
                continue
            component_id = _stable_id(skill_path, "optional", path)
            components.append(InventoryComponent(
                id=component_id, path=path, role="optional", load_policy="on_demand",
                characters=len(optional_content), tokens=counter(optional_content),
            ))
            contents[component_id] = optional_content
            owned.add(path)
        inventory.append(InventoryItem(
            id=_stable_id(skill_path, "skill"), path=skill_path, kind="skill",
            name=name, description=description,
            harnesses=_skill_harnesses(skill_path), load_policy="progressive",
            characters=sum(item.characters for item in components),
            tokens=sum(item.tokens for item in components), components=components,
        ))

    for path, content in sorted(texts.items()):
        if path in owned:
            continue
        detection = (
            ("agent", ["claude_code", "github_copilot"], "on_demand")
            if path in catalog_agent_paths
            else _detection(path)
        )
        if detection is None:
            continue
        kind, harnesses, policy = detection
        if kind == "mcp_config":
            servers = _mcp_servers(path, content)
            if not servers:
                warnings.append(ScanWarning(code="mcp_config_unreadable", message="No safe MCP server metadata could be read", path=path))
            components: list[InventoryComponent] = []
            if servers:
                safe_metadata = "\n".join(
                    f"{server.name}\t{server.transport}" for server in servers
                )
                component_id = _stable_id(path, kind, "metadata")
                components.append(InventoryComponent(
                    id=component_id, path=path, role="metadata",
                    load_policy="configuration_only",
                    characters=len(safe_metadata), tokens=counter(safe_metadata),
                ))
                contents[component_id] = safe_metadata
            inventory.append(InventoryItem(
                id=_stable_id(path, kind), path=path, kind=kind, harnesses=harnesses,
                load_policy=policy, components=components, mcp_servers=servers,
                accounting_note=(
                    "Connection configuration is excluded from prompt totals; "
                    "tool schemas are unavailable without contacting the server."
                ),
            ))
            continue
        component_id = _stable_id(path, kind)
        display_name, description = _frontmatter_metadata(content)
        if kind == "agent" and path in catalog_agent_paths and (
            display_name is None or description is None
        ):
            warnings.append(ScanWarning(
                code="invalid_agent", message="Catalog agent requires name and description frontmatter",
                path=path,
            ))
            continue
        component = InventoryComponent(
            id=component_id, path=path, role=kind, load_policy=policy,
            characters=len(content), tokens=counter(content),
        )
        contents[component_id] = content
        inventory.append(InventoryItem(
            id=component_id, path=path, kind=kind, harnesses=harnesses,
            name=display_name, description=description,
            load_policy=policy, characters=component.characters, tokens=component.tokens,
            components=[component],
        ))

    inventory.sort(key=lambda item: (item.path, item.kind))
    totals: dict[str, int] = {}
    for item in inventory:
        if item.tokens is not None:
            totals[item.kind] = totals.get(item.kind, 0) + item.tokens
    totals["all_discovered_text"] = sum(totals.values())
    report = RepositoryReport(
        repository=identity, method=method_info(encoding), analyzer_version=settings.analyzer_version,
        inventory=inventory, metadata_tokens=_metadata_token_total(inventory, counter),
        category_totals=totals,
        warnings=_aggregate_warnings(warnings),
        scan=ScanStats(
            archive_members=(
                archive_member_count if archive_member_count is not None else len(members)
            ),
            relevant_files=len(texts), relevant_bytes=total_bytes
        ),
    )
    return SnapshotAnalysis(
        report=report, contents=contents, file_bytes=file_bytes,
        weight=total_bytes + len(report.model_dump_json()),
    )


def retokenize_analysis(
    analysis: SnapshotAnalysis, encoding: str,
) -> SnapshotAnalysis:
    """Recount a discovered snapshot without downloading or parsing it again."""
    counter = counter_for(encoding)
    inventory: list[InventoryItem] = []
    for item in analysis.report.inventory:
        components: list[InventoryComponent] = []
        for component in item.components:
            content = analysis.contents.get(component.id, "")
            if item.kind == "skill" and component.role == "metadata":
                tokens = counter(item.name or "") + counter(item.description or "")
            else:
                tokens = counter(content)
            components.append(component.model_copy(update={"tokens": tokens}))
        tokens = None if item.tokens is None else sum(
            component.tokens for component in components
        )
        inventory.append(item.model_copy(update={
            "components": components,
            "tokens": tokens,
        }))

    totals: dict[str, int] = {}
    for item in inventory:
        if item.tokens is not None:
            totals[item.kind] = totals.get(item.kind, 0) + item.tokens
    totals["all_discovered_text"] = sum(totals.values())
    report = analysis.report.model_copy(update={
        "method": method_info(encoding),
        "inventory": inventory,
        "metadata_tokens": _metadata_token_total(inventory, counter),
        "category_totals": totals,
        "cached": False,
    })
    return SnapshotAnalysis(
        report=report,
        contents=analysis.contents,
        file_bytes=analysis.file_bytes,
        weight=sum(analysis.file_bytes.values()) + len(report.model_dump_json()),
    )


class RepositoryManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.gateway = GitHubGateway(settings)
        self.cache = AsyncTTLCache(
            settings.report_cache_entries, settings.report_cache_bytes,
            settings.report_cache_ttl_seconds,
        )
        self.badge_cache = AsyncTTLCache(
            settings.badge_cache_entries, settings.badge_cache_bytes,
            settings.badge_cache_ttl_seconds,
        )
        self.singleflight = SingleFlight()
        self.snapshot_singleflight = SingleFlight()
        self.resolution_cache = AsyncTTLCache(
            max(128, settings.report_cache_entries * 4), 4 * 1024 * 1024, 300,
        )
        self.resolution_singleflight = SingleFlight()
        self.public_cache = AsyncTTLCache(
            max(128, settings.report_cache_entries * 4), 1024 * 1024, 300,
        )
        self.public_singleflight = SingleFlight()
        self.scan_slots = asyncio.Semaphore(settings.repo_scan_concurrent)
        self.scan_admission_lock = asyncio.Lock()
        self.scan_admitted = 0
        self.scan_admission_limit = settings.repo_scan_concurrent * 2
        self.scan_executor = ThreadPoolExecutor(
            max_workers=settings.repo_scan_concurrent,
            thread_name_prefix="repository-scan",
        )
        self.quota = SlidingQuota()

    def close(self) -> None:
        self.scan_executor.shutdown(wait=True, cancel_futures=True)

    async def _run_in_worker(self, function: Callable[[], _T]) -> _T:
        future = self.scan_executor.submit(function)
        cancelled = False
        while not future.done():
            try:
                await asyncio.sleep(0.025)
            except asyncio.CancelledError:
                # Keep the archive valid until its worker has stopped reading it.
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError()
        return future.result()

    async def _analyze_in_worker(
        self,
        source: bytes | BinaryIO | SnapshotSource,
        identity: RepositoryIdentity,
        encoding: str,
    ) -> SnapshotAnalysis:
        if isinstance(source, SnapshotSource):
            function = partial(
                analyze_archive, source.archive, identity, encoding, self.settings,
                archive_member_count=source.archive_members,
                initial_warnings=source.warnings,
            )
        else:
            function = partial(analyze_archive, source, identity, encoding, self.settings)
        return await self._run_in_worker(function)

    def cache_key(self, identity: RepositoryIdentity) -> str:
        return "|".join((
            identity.owner.lower(), identity.name.lower(), identity.commit_sha.lower(),
            self.settings.analyzer_version,
        ))

    @staticmethod
    def public_cache_key(owner: str, repository: str) -> str:
        return f"{owner.lower()}|{repository.lower()}"

    async def _remember_public(self, owner: str, repository: str) -> None:
        key = self.public_cache_key(owner, repository)
        await self.public_cache.set(key, True, len(key) + 1)

    async def _ensure_public(self, owner: str, repository: str) -> None:
        key = self.public_cache_key(owner, repository)
        if await self.public_cache.get(key) is not None:
            return

        async def verify() -> None:
            if await self.public_cache.get(key) is not None:
                return
            await self.gateway.public_metadata(owner, repository)
            await self._remember_public(owner, repository)

        await self.public_singleflight.run(key, verify)

    async def ensure_public(self, owner: str, repository: str) -> None:
        """Verify the public-only boundary without downloading a snapshot."""
        await self._ensure_public(owner, repository)

    @asynccontextmanager
    async def _scan_admission(self):
        async with self.scan_admission_lock:
            if self.scan_admitted >= self.scan_admission_limit:
                raise ServiceProblem(
                    503, "repository_busy",
                    "repository analysis capacity is full; retry shortly",
                    5,
                )
            self.scan_admitted += 1
        try:
            async with self.scan_slots:
                yield
        finally:
            async with self.scan_admission_lock:
                self.scan_admitted -= 1

    def badge_cache_key(
        self, owner: str, repository: str, sha: str,
        subdirectory: str | None, encoding: str,
    ) -> str:
        return "|".join((
            owner.lower(), repository.lower(), sha.lower(), encoding,
            subdirectory or "", self.settings.analyzer_version,
        ))

    async def badge_summary(
        self, owner: str, repository: str, ref: str | None,
        subdirectory: str | None, encoding: str | None, ip: str,
    ) -> BadgeSummary:
        """Return a compact summary, resolving moving refs before cache lookup."""
        if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repository):
            raise ServiceProblem(422, "invalid_repository", "invalid GitHub repository")
        mutable_ref = ref is None or _SHA.fullmatch(ref) is None
        if mutable_ref:
            resolved = await self.resolve(
                RepositoryResolveRequest(
                    repository=f"{owner}/{repository}", ref=ref,
                    subdirectory=subdirectory, encoding=encoding,
                ),
                ip,
            )
            owner = resolved.repository.owner
            repository = resolved.repository.name
            sha = resolved.repository.commit_sha
            normalized_path = resolved.repository.subdirectory
        else:
            sha = ref.lower()
            normalized_path = normalize_subdirectory(subdirectory)

        selected = selected_encoding(encoding, self.settings)
        key = self.badge_cache_key(
            owner, repository, sha, normalized_path, selected
        )
        cached = await self.badge_cache.get(key)
        if cached is not None:
            return BadgeSummary(
                commit_sha=cached.commit_sha,
                metadata_tokens=cached.metadata_tokens,
                total_tokens=cached.total_tokens,
                mutable_ref=mutable_ref,
            )

        analysis = await self.get_cached(
            owner, repository, sha, normalized_path, selected
        )
        if analysis is None:
            analysis = await self.get(
                owner, repository, sha, normalized_path, selected, ip,
                include_ip_quota=False,
            )
        summary = BadgeSummary(
            commit_sha=sha,
            metadata_tokens=analysis.report.metadata_tokens,
            total_tokens=analysis.report.category_totals.get("all_discovered_text", 0),
            mutable_ref=mutable_ref,
        )
        group = f"{owner.lower()}|{repository.lower()}"
        weight = 160 + len(normalized_path or "") + len(selected)
        await self.badge_cache.set(
            key, summary, weight,
            group=group,
            max_group_entries=self.settings.badge_cache_repo_entries,
        )
        return summary

    async def resolve(self, request: RepositoryResolveRequest, ip: str) -> RepositoryResolveResponse:
        key = request.model_dump_json()
        cached = await self.resolution_cache.get(key)
        if cached is not None:
            return cached

        async def load() -> RepositoryResolveResponse:
            second = await self.resolution_cache.get(key)
            if second is not None:
                return second
            await self._consume_resolution_quota()
            resolved = await self.gateway.resolve(request, self.settings)
            await self._remember_public(
                resolved.repository.owner, resolved.repository.name
            )
            await self.resolution_cache.set(
                key, resolved, len(resolved.model_dump_json())
            )
            return resolved

        try:
            return await asyncio.wait_for(
                self.resolution_singleflight.run(key, load),
                self.settings.repo_timeout_seconds,
            )
        except asyncio.TimeoutError as error:
            raise ServiceProblem(503, "repository_timeout", "repository resolution timed out") from error

    async def get(
        self, owner: str, repository: str, sha: str, subdirectory: str | None,
        encoding: str | None, ip: str, *, include_ip_quota: bool = True,
        progress: ProgressCallback | None = None,
    ) -> SnapshotAnalysis:
        if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repository) or not _SHA.fullmatch(sha):
            raise ServiceProblem(422, "invalid_snapshot", "invalid repository snapshot")
        normalized_path = normalize_subdirectory(subdirectory)
        selected = selected_encoding(encoding, self.settings)
        requested_identity = RepositoryIdentity(
            owner=owner, name=repository, commit_sha=sha.lower(),
            html_url=(
                f"https://github.com/{owner}/{repository}/tree/{sha.lower()}"
                + (f"/{quote(normalized_path, safe='/')}" if normalized_path else "")
            ),
            subdirectory=normalized_path,
        )
        full_identity = requested_identity.model_copy(update={
            "subdirectory": None,
            "html_url": f"https://github.com/{owner}/{repository}/tree/{sha.lower()}",
        })
        await _notify_progress(progress, "cache", "Checking the repository report cache")
        key = self.cache_key(full_identity)
        cached: CachedRepository | None = await self.cache.get(key)
        if cached is not None and selected in cached.analyses:
            await _notify_progress(
                progress, "complete", "Cached report ready", cached=True
            )
            return scope_analysis(cached.analyses[selected], normalized_path, cached=True)

        async def load() -> SnapshotAnalysis:
            second: CachedRepository | None = await self.cache.get(key)
            if second is not None and selected in second.analyses:
                return second.analyses[selected]

            async def discover() -> CachedRepository:
                existing: CachedRepository | None = await self.cache.get(key)
                if existing is not None:
                    return existing
                async with self._scan_admission():
                    await self._consume_repo_quota(ip, include_ip_quota)
                    await _notify_progress(
                        progress, "verify", "Verifying that the GitHub repository is public"
                    )
                    await self._ensure_public(owner, repository)
                    source = (
                        await self.gateway.archive(
                            owner, repository, sha, progress=progress
                        )
                        if progress is not None
                        else await self.gateway.archive(owner, repository, sha)
                    )
                    handle = source.archive if isinstance(source, SnapshotSource) else source
                    try:
                        await _notify_progress(
                            progress, "analyze",
                            "Discovering harness artifacts and counting tokens",
                        )
                        result = await self._analyze_in_worker(
                            source, full_identity, selected
                        )
                    finally:
                        if not isinstance(handle, bytes):
                            handle.close()
                entry = CachedRepository({selected: result})
                await self.cache.set(key, entry, entry.weight)
                return entry

            entry = await self.snapshot_singleflight.run(key, discover)
            result = entry.analyses.get(selected)
            if result is None:
                base = next(iter(entry.analyses.values()))
                await _notify_progress(
                    progress, "analyze",
                    f"Recounting retained components with {selected}",
                )
                result = await self._run_in_worker(
                    partial(retokenize_analysis, base, selected)
                )
                entry.analyses[selected] = result
                await self.cache.set(key, entry, entry.weight)
            return result

        try:
            full_analysis = await asyncio.wait_for(
                self.singleflight.run(f"{key}|{selected}", load),
                self.settings.repo_timeout_seconds,
            )
            await _notify_progress(progress, "complete", "Repository report ready")
            return scope_analysis(full_analysis, normalized_path, cached=False)
        except asyncio.TimeoutError as error:
            raise ServiceProblem(503, "repository_timeout", "repository analysis timed out") from error

    async def get_cached(
        self, owner: str, repository: str, sha: str | None,
        subdirectory: str | None, encoding: str | None,
    ) -> SnapshotAnalysis | None:
        """Return an already verified snapshot without another GitHub request."""
        if (
            sha is None or not _NAME.fullmatch(owner)
            or not _NAME.fullmatch(repository) or not _SHA.fullmatch(sha)
        ):
            return None
        normalized_path = normalize_subdirectory(subdirectory)
        selected = selected_encoding(encoding, self.settings)
        identity = RepositoryIdentity(
            owner=owner, name=repository, commit_sha=sha.lower(),
            html_url=f"https://github.com/{owner}/{repository}/tree/{sha.lower()}",
        )
        cached: CachedRepository | None = await self.cache.get(self.cache_key(identity))
        if cached is None or selected not in cached.analyses:
            return None
        return scope_analysis(cached.analyses[selected], normalized_path, cached=True)

    async def _consume_resolution_quota(self) -> None:
        if not self.settings.quotas_enabled:
            return
        try:
            await self.quota.consume(
                "repo:resolution:global",
                self.settings.repo_ref_resolutions_per_hour,
                3600,
            )
        except QuotaExceeded as error:
            raise ServiceProblem(
                429, "repository_resolution_quota_exceeded",
                "repository resolution quota exceeded", error.retry_after,
            ) from error

    async def _consume_repo_quota(
        self, ip: str, include_ip: bool = True,
    ) -> None:
        if not self.settings.quotas_enabled:
            return
        limits = [("repo:global", self.settings.repo_global_misses_per_hour, 3600)]
        if include_ip:
            limits.insert(
                0,
                (f"repo:ip:{ip}", self.settings.repo_ip_misses_per_hour, 3600),
            )
        try:
            await self.quota.consume_many(limits)
        except QuotaExceeded as error:
            raise ServiceProblem(429, "repository_quota_exceeded", "repository analysis quota exceeded", error.retry_after) from error
