"""Code adapter: Git identity/isolation and deterministic PHP 8.4 extraction.

Parser adapter returns normalized symbol records only. It never takes domain
decisions: trust, lifecycle and eligibility stay in the core.
"""

from . import git, php

__all__ = ["git", "php"]
