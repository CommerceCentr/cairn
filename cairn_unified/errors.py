"""Помилки домену. Код стабільний для CLI, MCP і тестів."""

from __future__ import annotations


class CairnError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 3, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "details": self.details}
