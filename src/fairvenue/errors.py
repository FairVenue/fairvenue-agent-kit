"""Public exception types without sensitive response-body rendering."""

from __future__ import annotations


class FairVenueError(Exception):
    """Base SDK error."""


class FairVenueApiError(FairVenueError):
    """Structured FairVenue API error."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retry_after_ms: int | None = None,
    ) -> None:
        self.code = code
        self.api_message = message
        self.status_code = status_code
        self.retry_after_ms = retry_after_ms
        super().__init__(f"FairVenue API error {code} (HTTP {status_code})")


class CredentialsError(FairVenueError):
    """Credential file validation or permission failure."""
