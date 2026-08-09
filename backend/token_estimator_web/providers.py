"""Turnstile validation and service-owned native token-count provider clients."""

from __future__ import annotations

import hashlib
from urllib.parse import quote

import httpx

from .cache import AsyncTTLCache, QuotaExceeded, SingleFlight, SlidingQuota
from .config import Settings
from .errors import ServiceProblem
from .repository import RepositoryManager
from .schemas import NativeCountRequest, NativeCountResponse


class NativeCountManager:
    def __init__(self, settings: Settings, repositories: RepositoryManager) -> None:
        self.settings = settings
        self.repositories = repositories
        self.cache = AsyncTTLCache(
            settings.native_cache_entries, settings.native_cache_entries,
            settings.native_cache_ttl_seconds,
        )
        self.singleflight = SingleFlight()
        self.quota = SlidingQuota()

    async def count(self, request: NativeCountRequest, ip: str) -> NativeCountResponse:
        await self._verify_turnstile(request.turnstile_token, ip)
        models, key = self._provider_configuration(request.provider)
        if not key or not models:
            raise ServiceProblem(503, "provider_disabled", f"{request.provider} token counting is not configured")
        if request.model not in models:
            raise ServiceProblem(422, "model_not_allowed", "model is not in the deployment allowlist")
        if len(request.item_ids) > self.settings.native_max_items:
            raise ServiceProblem(413, "too_many_native_items", "too many native-count items")
        if len(set(request.item_ids)) != len(request.item_ids):
            raise ServiceProblem(422, "duplicate_item_ids", "native-count item IDs must be unique")
        analysis = await self.repositories.get(
            request.snapshot.owner, request.snapshot.repository, request.snapshot.commit_sha,
            request.snapshot.subdirectory, request.snapshot.encoding, ip,
        )
        requested = set(request.item_ids)
        unknown = requested - analysis.contents.keys()
        if unknown:
            raise ServiceProblem(422, "unknown_item_ids", "one or more selected items do not belong to this snapshot")
        ordered_ids = [item_id for item_id in analysis.contents if item_id in requested]
        texts = [analysis.contents[item_id] for item_id in ordered_ids]
        total_bytes = sum(len(value.encode("utf-8")) for value in texts)
        if total_bytes > self.settings.native_max_bytes:
            raise ServiceProblem(413, "native_content_too_large", "selected content exceeds the native-count limit")
        digest = hashlib.sha256()
        digest.update(f"{request.provider}\0{request.model}\0raw-selection-v1".encode())
        for item_id, value in zip(ordered_ids, texts):
            digest.update(item_id.encode())
            digest.update(b"\0")
            digest.update(value.encode())
            digest.update(b"\0")
        cache_key = digest.hexdigest()
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return NativeCountResponse(
                provider=request.provider, model=request.model, item_ids=ordered_ids,
                input_tokens=cached, cached=True,
            )

        async def load() -> int:
            second = await self.cache.get(cache_key)
            if second is not None:
                return second
            await self._consume_quota(ip)
            value = await self._provider_count(request.provider, request.model, texts, key)
            await self.cache.set(cache_key, value)
            return value

        tokens = await self.singleflight.run(cache_key, load)
        return NativeCountResponse(
            provider=request.provider, model=request.model, item_ids=ordered_ids,
            input_tokens=tokens,
        )

    def _provider_configuration(self, provider: str) -> tuple[tuple[str, ...], str]:
        if provider == "anthropic":
            return self.settings.anthropic_models, self.settings.anthropic_api_key
        return self.settings.gemini_models, self.settings.gemini_api_key

    async def _verify_turnstile(self, token: str, ip: str) -> None:
        if not self.settings.turnstile_required:
            return
        if not self.settings.turnstile_secret_key or not token:
            raise ServiceProblem(403, "verification_required", "complete the verification challenge")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                response = await client.post(
                    "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                    json={
                        "secret": self.settings.turnstile_secret_key,
                        "response": token,
                        "remoteip": ip,
                    },
                )
            result = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ServiceProblem(503, "verification_unavailable", "verification service is unavailable") from error
        if response.status_code >= 400 or result.get("success") is not True:
            raise ServiceProblem(403, "verification_failed", "verification failed; request a fresh challenge")
        if result.get("action") != "native_count":
            raise ServiceProblem(403, "verification_failed", "verification action did not match")
        expected = self.settings.turnstile_expected_hostname
        if expected and result.get("hostname") != expected:
            raise ServiceProblem(403, "verification_failed", "verification hostname did not match")

    async def _consume_quota(self, ip: str) -> None:
        if not self.settings.quotas_enabled:
            return
        try:
            await self.quota.consume(
                f"native:ip:{ip}", self.settings.native_ip_misses_per_hour, 3600
            )
            await self.quota.consume(
                "native:global", self.settings.native_global_misses_per_day, 86_400
            )
        except QuotaExceeded as error:
            raise ServiceProblem(429, "native_quota_exceeded", "native token-count quota exceeded", error.retry_after) from error

    async def _provider_count(
        self, provider: str, model: str, texts: list[str], api_key: str
    ) -> int:
        timeout = httpx.Timeout(self.settings.request_timeout_seconds, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if provider == "anthropic":
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages/count_tokens",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [{
                                "role": "user",
                                "content": [{"type": "text", "text": text} for text in texts],
                            }],
                        },
                    )
                    field = "input_tokens"
                else:
                    response = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:countTokens",
                        headers={"x-goog-api-key": api_key, "content-type": "application/json"},
                        json={"contents": [{"role": "user", "parts": [{"text": text} for text in texts]}]},
                    )
                    field = "totalTokens"
        except httpx.TimeoutException as error:
            raise ServiceProblem(503, "provider_timeout", f"{provider} token counting timed out") from error
        except httpx.HTTPError as error:
            raise ServiceProblem(502, "provider_unavailable", f"{provider} token counting is unavailable") from error
        if response.status_code == 429:
            try:
                retry = max(1, int(response.headers.get("retry-after", "60")))
            except ValueError:
                retry = 60
            raise ServiceProblem(429, "provider_rate_limited", f"{provider} rate limit reached", retry)
        if response.status_code >= 400:
            raise ServiceProblem(502, "provider_error", f"{provider} returned HTTP {response.status_code}")
        try:
            value = response.json().get(field)
        except ValueError as error:
            raise ServiceProblem(502, "provider_invalid_response", f"{provider} returned invalid JSON") from error
        if not isinstance(value, int) or value < 0:
            raise ServiceProblem(502, "provider_invalid_response", f"{provider} returned an invalid token count")
        return value
