"""Typed settings entry point for v2 packages (re-exports the canonical Settings object)."""

from __future__ import annotations

from app.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
