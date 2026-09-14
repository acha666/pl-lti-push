"""Diagnostics containing only controlled text, safe to display without secrets."""


class UserError(ValueError):
    """An actionable, sanitized configuration or operational failure."""
