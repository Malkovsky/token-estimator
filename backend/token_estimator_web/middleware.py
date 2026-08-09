"""Request safeguards for the anonymous public service."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings


def error_payload(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or []}}


def client_ip(request: Request, settings: Settings) -> str:
    direct = request.client.host if request.client else "unknown"
    if settings.proxy_mode == "render":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return direct


class RequestTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers", [])).get(b"content-length")
        if length:
            try:
                if int(length) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content=error_payload("request_too_large", "request body exceeds the configured limit"),
        )
        await response(scope, receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        if (
            not self.settings.quotas_enabled
            or request.method not in {"POST", "PUT", "PATCH"}
            or not request.url.path.startswith("/api/v1/")
        ):
            return await call_next(request)
        now = time.monotonic()
        key = client_ip(request, self.settings)
        window = self.requests[key]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self.settings.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60"},
                content=error_payload("rate_limited", "too many requests; retry after 60 seconds"),
            )
        window.append(now)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' "
            "https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com",
        )
        return response
