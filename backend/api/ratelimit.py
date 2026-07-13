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
        check_bucket(f"{_client_ip(request)}:{request.url.path}",
                     max_calls, window_seconds)
    return Depends(_dep)


def check_bucket(key: str, max_calls: int, window_seconds: int,
                 message: str = "too many requests — please slow down") -> None:
    """Sliding-window check on an ARBITRARY key (IP, phone number, …).
    Raises 429 with Retry-After when the bucket is full; otherwise records
    the hit. Phase 8-2: lets endpoints layer identity-keyed limits (e.g. one
    OTP budget per PHONE NUMBER regardless of source IP) on top of the
    per-IP dependency."""
    now = time.monotonic()
    cutoff = now - window_seconds
    dq = _hits[(key, "")]
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= max_calls:
        retry_after = int(dq[0] + window_seconds - now) + 1
        raise HTTPException(429, message, headers={"Retry-After": str(retry_after)})
    dq.append(now)


# ── Phase 8-2: strict abuse rules (OTP toll fraud, webhook HMAC probing) ──────
# These are HARD limits meant for the internet-facing deploy. In hermetic test
# environments (GI_DOTENV=0 — service_tests, Playwright, CI) they are relaxed
# so functional suites can exercise the OTP/webhook flows freely; the suites
# that test THE LIMITS THEMSELVES force them on via GI_FORCE_STRICT_LIMITS=1.
def strict_limits_enabled() -> bool:
    import os
    if os.environ.get("GI_FORCE_STRICT_LIMITS") == "1":
        return True
    return os.environ.get("GI_DOTENV", "").strip() != "0"


class PenaltyBox:
    """Strike-based temporary IP ban: `threshold` strikes inside `window`
    seconds ⇒ the IP is banned for `ban_seconds`. Used for sources that keep
    sending invalid HMAC signatures to the WhatsApp webhook — after the ban
    trips, requests are refused before any body parsing happens."""

    def __init__(self, threshold: int, window_seconds: int, ban_seconds: int):
        self.threshold = threshold
        self.window = window_seconds
        self.ban = ban_seconds
        self._strikes: dict[str, deque[float]] = defaultdict(deque)
        self._banned_until: dict[str, float] = {}

    def banned_for(self, ip: str) -> int | None:
        """Seconds remaining on an active ban, else None."""
        until = self._banned_until.get(ip)
        if until is None:
            return None
        remaining = until - time.monotonic()
        if remaining <= 0:
            self._banned_until.pop(ip, None)
            self._strikes.pop(ip, None)
            return None
        return int(remaining) + 1

    def strike(self, ip: str) -> bool:
        """Record one violation; returns True when this strike trips the ban."""
        now = time.monotonic()
        dq = self._strikes[ip]
        while dq and dq[0] < now - self.window:
            dq.popleft()
        dq.append(now)
        if len(dq) >= self.threshold:
            self._banned_until[ip] = now + self.ban
            return True
        return False


def client_ip(request: Request) -> str:
    """Public alias — same CF-Connecting-IP → X-Real-IP → peer resolution."""
    return _client_ip(request)
