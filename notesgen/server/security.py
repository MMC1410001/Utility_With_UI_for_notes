"""Keep the local server local.

Binding to 127.0.0.1 stops other machines reaching it, but it does not stop
*other pages in this browser* - any website the user has open can POST to
http://127.0.0.1:8787. The endpoints here start jobs, write files, and spend
money on model calls, so they need more than a loopback bind.

So: a token, generated on first run and stored in the user's home directory.
The web UI gets it injected into the page it is served; the extension is told
to paste it once. `/api/health` is deliberately open so the extension can find
the server before it has a token.

`EventSource` cannot set request headers and neither can a plain download
link, so the token is accepted as a `?token=` query parameter too. That is a
real trade-off - query strings land in logs - but this server logs to the
user's own terminal, and the alternative is no live progress at all.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

HEADER = "X-Notesgen-Token"
TOKEN_FILE = Path.home() / ".notesgen" / "server-token"

# Paths reachable without a token. Health is how the extension finds us; the
# static UI has to load before it can send anything.
OPEN_PATHS = ("/api/health",)


def load_or_create_token() -> str:
    """A stable token, so the extension does not need re-pairing every restart."""
    from_env = os.environ.get("NOTESGEN_TOKEN")
    if from_env:
        return from_env.strip()

    if TOKEN_FILE.exists():
        existing = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(24)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return token


class TokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api/") or path in OPEN_PATHS:
            return await call_next(request)

        supplied = request.headers.get(HEADER) or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, self.token):
            return Response(
                '{"detail":"missing or invalid token"}',
                status_code=401,
                media_type="application/json",
            )
        return await call_next(request)


class PrivateNetworkMiddleware(BaseHTTPMiddleware):
    """Let the extension reach 127.0.0.1 at all.

    Chrome's Private Network Access blocks a request from a public origin (the
    extension, or a page on udemy.com) to a loopback address unless the
    preflight is answered with this header. Starlette's CORSMiddleware does not
    emit it, so without this the extension silently cannot talk to the server -
    and the failure looks like the server being down.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


def require_local(request: Request) -> None:
    """Refuse anything that did not come from this machine."""
    client = request.client.host if request.client else None
    if client not in ("127.0.0.1", "::1", "localhost", None):
        raise HTTPException(status_code=403, detail="local requests only")
