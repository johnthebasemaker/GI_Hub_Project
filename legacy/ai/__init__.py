"""
ai/ — Local Ollama-powered features for the GI Lightning Hub
=============================================================
Strictly additive. Importing this package has no side effects on routing,
caching, or the database layer. If the Ollama server is unreachable, every
public helper degrades to a clean error path that callers can surface to the
user — no exceptions leak into normal page render.

Why standard library, not the `ollama` pip package
--------------------------------------------------
The project's pinned requirements.txt is small on purpose. We use the
Ollama HTTP API directly via `urllib.request` so adding AI features requires
zero new dependencies. If the team later wants the official `ollama` client
for richer streaming, the swap is contained to `client.py`.
"""

from .client import (
    ollama_health,
    ollama_generate,
    ollama_stream,
    OLLAMA_AVAILABLE,
    MODEL_CODER,
    MODEL_CHAT,
    MODEL_EMBED,
)
from .safety import is_safe_select, scrub_sql

__all__ = [
    "ollama_health",
    "ollama_generate",
    "ollama_stream",
    "OLLAMA_AVAILABLE",
    "MODEL_CODER",
    "MODEL_CHAT",
    "MODEL_EMBED",
    "is_safe_select",
    "scrub_sql",
]
