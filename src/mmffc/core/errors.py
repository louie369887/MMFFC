"""Stable exit codes (CLI.md section 2.3) and error types.

Exit codes are part of the CLI contract; changing them is a breaking change.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_SCHEMA = 3
EXIT_NETWORK = 4
EXIT_CONFLICT = 5
EXIT_PERMISSION = 6
EXIT_DEPENDENCY = 7
EXIT_INTEGRITY = 8
EXIT_INTERRUPT = 130
EXIT_SIGPIPE = 141


class MMFFCError(Exception):
    """Base MMFFC error carrying a stable exit code."""

    exit_code = EXIT_ERROR

    def __init__(self, message: str, *, exit_code: int | None = None, **details) -> None:
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code
        self.details = details

    def to_dict(self) -> dict:
        return {"error": self.message, "exit_code": self.exit_code, **self.details}


class UsageError(MMFFCError):
    """Usage / parameter error -> exit 2."""

    exit_code = EXIT_USAGE


class SchemaError(MMFFCError):
    """Schema validation failure -> exit 3."""

    exit_code = EXIT_SCHEMA


class NetworkError(MMFFCError):
    """Network / API / download error -> exit 4."""

    exit_code = EXIT_NETWORK


class NotFoundError(NetworkError):
    """Remote resource not found (HTTP 404)."""

    def __init__(self, message: str = "Resource not found", **details) -> None:
        super().__init__(message, http_status=404, **details)


class ConflictError(MMFFCError):
    """File conflict / already exists -> exit 5."""

    exit_code = EXIT_CONFLICT


class PermissionDeniedError(MMFFCError):
    """Permission error -> exit 6."""

    exit_code = EXIT_PERMISSION


class DependencyError(MMFFCError):
    """Dependency not satisfied -> exit 7."""

    exit_code = EXIT_DEPENDENCY


class IntegrityError(MMFFCError):
    """Save / archive integrity risk -> exit 8."""

    exit_code = EXIT_INTEGRITY
