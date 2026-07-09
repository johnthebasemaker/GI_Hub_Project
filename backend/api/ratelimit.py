"""
backend/api/ratelimit.py — a tiny in-memory rate limiter for the public auth
endpoints (login / register / 2fa), which become internet-facing after deploy.

Dependency-free on purpose (a per-endpoint FastAPI dependency). Keyed by client
IP, resolved in priority order:
  1. `CF-Connecting-IP` — the real client IP Cloudflare injects when traffic
     comes through a Cloudflare Tunnel. WITHOUT this every remote tester shares
     the tunnel's single egress IP and trips the limit together.
  2. `X-Real-IP` — set by our nginx deploy (`proxy_set_header X-Real-IP
     $remote_addr`); the client can't forge it through the proxy.
  3. the TCP peer — for a direct/no-proxy run.

Both proxy headers are trusted because the only public path to this service is
through Cloudflare (tunnel) or nginx; a direct-to-origin caller on the LAN could
spoof them, which is an accepted local-network trade-off (noted in the backlog).

CAVEAT: the store is per-process. With N uvicorn workers the effective ceiling
is N × the configured limit — fine as basic brute-force/abuse protection, but
for a hard cross-worker limit use a shared store (e.g. Redis). Good enough for
a single-box deploy; noted in the improvement backlog.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # Cloudflare Tunnel first — otherwise all tunnelled testers key on one IP.
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else "unknown"


def rate_limit(max_calls: int, window_seconds: int):
    """FastAPI dependency: at most `max_calls` per `window_seconds` per client
    IP per endpoint path. Raises 429 (with Retry-After) when exceeded."""
    async def _dep(request: Request):
        key = (_client_ip(request), request.url.path)
        now = time.monotonic()
        cutoff = now - window_seconds
        dq = _hits[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_calls:
            retry_after = int(dq[0] + window_seconds - now) + 1
            raise HTTPException(429, "too many requests — please slow down",
                                headers={"Retry-After": str(retry_after)})
        dq.append(now)
        if not dq:                       # keep the dict from growing unbounded
            _hits.pop(key, None)
    return Depends(_dep)
