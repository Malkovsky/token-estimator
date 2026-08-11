"""Environment-backed configuration with conservative public-service defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(part.strip() for part in os.getenv(name, default).split(",") if part.strip())


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


@dataclass(frozen=True)
class Settings:
    default_encoding: str
    max_content_bytes: int
    max_request_bytes: int
    max_items: int
    max_tools: int
    max_concurrent: int
    request_timeout_seconds: int
    quotas_enabled: bool
    rate_limit_per_minute: int
    allowed_origins: tuple[str, ...]
    proxy_mode: str
    github_token: str
    github_api_base: str
    repo_fetch_mode: str
    repo_timeout_seconds: int
    repo_archive_max_bytes: int
    repo_archive_memory_bytes: int
    repo_max_total_bytes: int
    repo_scan_concurrent: int
    repo_max_members: int
    repo_max_relevant_files: int
    repo_max_content_bytes: int
    repo_max_file_bytes: int
    report_cache_entries: int
    report_cache_bytes: int
    report_cache_ttl_seconds: int
    badge_cache_entries: int
    badge_cache_bytes: int
    badge_cache_ttl_seconds: int
    badge_cache_repo_entries: int
    repo_ref_resolutions_per_hour: int
    repo_ip_misses_per_hour: int
    repo_global_misses_per_hour: int
    native_max_bytes: int
    native_max_items: int
    native_cache_entries: int
    native_cache_ttl_seconds: int
    native_ip_misses_per_hour: int
    native_global_misses_per_day: int
    anthropic_api_key: str
    anthropic_models: tuple[str, ...]
    gemini_api_key: str
    gemini_models: tuple[str, ...]
    turnstile_site_key: str
    turnstile_secret_key: str
    turnstile_expected_hostname: str
    turnstile_required: bool
    analyzer_version: str = "repo-inventory-v10"

    @classmethod
    def from_env(cls) -> "Settings":
        max_content = _positive_int("TOKEN_ESTIMATOR_MAX_CONTENT_BYTES", 10 * 1024 * 1024)
        proxy_mode = os.getenv("TOKEN_ESTIMATOR_PROXY_MODE", "direct")
        if proxy_mode not in {"direct", "render"}:
            raise RuntimeError("TOKEN_ESTIMATOR_PROXY_MODE must be direct or render")
        repo_fetch_mode = os.getenv("TOKEN_ESTIMATOR_REPO_FETCH_MODE", "auto").lower()
        if repo_fetch_mode not in {"auto", "git", "archive"}:
            raise RuntimeError(
                "TOKEN_ESTIMATOR_REPO_FETCH_MODE must be auto, git, or archive"
            )
        required = os.getenv("TOKEN_ESTIMATOR_TURNSTILE_REQUIRED", "true").lower() not in {
            "0", "false", "no"
        }
        settings = cls(
            default_encoding=os.getenv("TOKEN_ESTIMATOR_DEFAULT_ENCODING", "o200k_base"),
            max_content_bytes=max_content,
            max_request_bytes=_positive_int(
                "TOKEN_ESTIMATOR_MAX_REQUEST_BYTES", max_content + 1024 * 1024
            ),
            max_items=_positive_int("TOKEN_ESTIMATOR_MAX_ITEMS", 1000),
            max_tools=_positive_int("TOKEN_ESTIMATOR_MAX_TOOLS", 5000),
            max_concurrent=_positive_int("TOKEN_ESTIMATOR_MAX_CONCURRENT", 4),
            request_timeout_seconds=_positive_int(
                "TOKEN_ESTIMATOR_REQUEST_TIMEOUT_SECONDS", 15
            ),
            quotas_enabled=_boolean("TOKEN_ESTIMATOR_QUOTAS_ENABLED", True),
            rate_limit_per_minute=_positive_int(
                "TOKEN_ESTIMATOR_RATE_LIMIT_PER_MINUTE", 60
            ),
            allowed_origins=_csv("TOKEN_ESTIMATOR_ALLOWED_ORIGINS", "http://localhost:5173"),
            proxy_mode=proxy_mode,
            github_token=os.getenv("GITHUB_TOKEN", ""),
            github_api_base=os.getenv("TOKEN_ESTIMATOR_GITHUB_API", "https://api.github.com"),
            repo_fetch_mode=repo_fetch_mode,
            repo_timeout_seconds=_positive_int("TOKEN_ESTIMATOR_REPO_TIMEOUT_SECONDS", 120),
            repo_archive_max_bytes=_positive_int(
                "TOKEN_ESTIMATOR_REPO_ARCHIVE_MAX_BYTES", 100 * 1024 * 1024
            ),
            repo_archive_memory_bytes=_positive_int(
                "TOKEN_ESTIMATOR_REPO_ARCHIVE_MEMORY_BYTES", 8 * 1024 * 1024
            ),
            repo_max_total_bytes=_positive_int(
                "TOKEN_ESTIMATOR_REPO_MAX_TOTAL_BYTES", 512 * 1024 * 1024
            ),
            repo_scan_concurrent=_positive_int(
                "TOKEN_ESTIMATOR_REPO_SCAN_CONCURRENT", 2
            ),
            repo_max_members=_positive_int("TOKEN_ESTIMATOR_REPO_MAX_MEMBERS", 50_000),
            repo_max_relevant_files=_positive_int(
                "TOKEN_ESTIMATOR_REPO_MAX_RELEVANT_FILES", 5000
            ),
            repo_max_content_bytes=_positive_int(
                "TOKEN_ESTIMATOR_REPO_MAX_CONTENT_BYTES", 20 * 1024 * 1024
            ),
            repo_max_file_bytes=_positive_int(
                "TOKEN_ESTIMATOR_REPO_MAX_FILE_BYTES", 1024 * 1024
            ),
            report_cache_entries=_positive_int("TOKEN_ESTIMATOR_REPORT_CACHE_ENTRIES", 128),
            report_cache_bytes=_positive_int(
                "TOKEN_ESTIMATOR_REPORT_CACHE_BYTES", 128 * 1024 * 1024
            ),
            report_cache_ttl_seconds=_positive_int(
                "TOKEN_ESTIMATOR_REPORT_CACHE_TTL_SECONDS", 7200
            ),
            badge_cache_entries=_positive_int(
                "TOKEN_ESTIMATOR_BADGE_CACHE_ENTRIES", 2048
            ),
            badge_cache_bytes=_positive_int(
                "TOKEN_ESTIMATOR_BADGE_CACHE_BYTES", 8 * 1024 * 1024
            ),
            badge_cache_ttl_seconds=_positive_int(
                "TOKEN_ESTIMATOR_BADGE_CACHE_TTL_SECONDS", 30 * 24 * 60 * 60
            ),
            badge_cache_repo_entries=_positive_int(
                "TOKEN_ESTIMATOR_BADGE_CACHE_REPO_ENTRIES", 16
            ),
            repo_ref_resolutions_per_hour=_positive_int(
                "TOKEN_ESTIMATOR_REPO_REF_RESOLUTIONS_PER_HOUR", 1000
            ),
            repo_ip_misses_per_hour=_positive_int(
                "TOKEN_ESTIMATOR_REPO_IP_MISSES_PER_HOUR", 10
            ),
            repo_global_misses_per_hour=_positive_int(
                "TOKEN_ESTIMATOR_REPO_GLOBAL_MISSES_PER_HOUR", 200
            ),
            native_max_bytes=_positive_int("TOKEN_ESTIMATOR_NATIVE_MAX_BYTES", 1024 * 1024),
            native_max_items=_positive_int("TOKEN_ESTIMATOR_NATIVE_MAX_ITEMS", 100),
            native_cache_entries=_positive_int("TOKEN_ESTIMATOR_NATIVE_CACHE_ENTRIES", 512),
            native_cache_ttl_seconds=_positive_int(
                "TOKEN_ESTIMATOR_NATIVE_CACHE_TTL_SECONDS", 86_400
            ),
            native_ip_misses_per_hour=_positive_int(
                "TOKEN_ESTIMATOR_NATIVE_IP_MISSES_PER_HOUR", 5
            ),
            native_global_misses_per_day=_positive_int(
                "TOKEN_ESTIMATOR_NATIVE_GLOBAL_MISSES_PER_DAY", 100
            ),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_models=_csv("TOKEN_ESTIMATOR_ANTHROPIC_MODELS"),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_models=_csv("TOKEN_ESTIMATOR_GEMINI_MODELS"),
            turnstile_site_key=os.getenv("TURNSTILE_SITE_KEY", ""),
            turnstile_secret_key=os.getenv("TURNSTILE_SECRET_KEY", ""),
            turnstile_expected_hostname=os.getenv("TURNSTILE_EXPECTED_HOSTNAME", ""),
            turnstile_required=required,
        )
        if settings.turnstile_required and (
            (settings.anthropic_api_key and settings.anthropic_models)
            or (settings.gemini_api_key and settings.gemini_models)
        ) and not (settings.turnstile_site_key and settings.turnstile_secret_key):
            raise RuntimeError("enabled native providers require Turnstile keys")
        if settings.repo_archive_memory_bytes > settings.repo_archive_max_bytes:
            raise RuntimeError(
                "TOKEN_ESTIMATOR_REPO_ARCHIVE_MEMORY_BYTES must not exceed "
                "TOKEN_ESTIMATOR_REPO_ARCHIVE_MAX_BYTES"
            )
        return settings
