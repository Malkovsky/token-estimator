"""Transport-neutral service failures mapped by the FastAPI adapter."""

from __future__ import annotations


class ServiceProblem(Exception):
    def __init__(
        self, status: int, code: str, message: str, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
