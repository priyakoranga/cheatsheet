import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(key: str, limit: int, window: int) -> None:
    """Allow `limit` hits per `window` seconds for `key`; otherwise raise 429.
    In-memory: fine for a single instance. Use Redis if you scale to several."""
    now = time.monotonic()
    with _lock:
        if len(_hits) > 20000:  # keep memory bounded
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > 3600]:
                del _hits[k]
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many attempts. Please try again later.")
        q.append(now)
