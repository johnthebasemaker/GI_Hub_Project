"""
backend/api/ai — the Intelligence Layer (Phase AI-0 foundation).

Async port of the legacy `ai/` package for the FastAPI stack. Same design
contract as legacy: zero heavy imports at module load, every feature degrades
to a friendly message when the local Ollama server is unreachable, and all
pure logic (safety gate, fuzzy matcher, prompts) stays framework-free and
unit-testable.

Modules:
  client.py     async Ollama HTTP client (httpx) + generation semaphore
  safety.py     safe-SQL gate for AI-generated queries (PG-hardened port)
  fuzzy.py      free-text → inventory match (pandas-free port)
  manual_qa.py  role-gated section retrieval over USER_MANUAL.md
  router.py     /ai endpoints (health + SSE assistant stream)
"""
