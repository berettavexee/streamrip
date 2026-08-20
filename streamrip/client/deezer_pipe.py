"""GraphQL Pipe API client for pipe.deezer.com.

The Pipe API is Deezer's modern GraphQL endpoint, distinct from both the
public REST API (api.deezer.com) and the legacy Gateway (gw-light.php).
It requires a short-lived JWT obtained by exchanging the ARL against
auth.deezer.com/login/renew.

Auth flow (derived from Android APK 9.0.16.3 reverse engineering):
  POST https://auth.deezer.com/login/renew?i=p&jo=p&rto=p
  Body: {"refresh_token": <arl>}
  → {"jwt": "<RS256 token, TTL 6 min>", "refresh_token": "<unchanged arl>"}

All Pipe requests carry:
  Authorization: Bearer <jwt>
  Content-Type: application/json
  Accept: multipart/mixed;deferSpec=20220824, application/graphql-response+json, application/json
"""

import asyncio
import base64
import json
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger("streamrip")

_RENEW_URL = "https://auth.deezer.com/login/renew"
_PIPE_URL = "https://pipe.deezer.com/api"

# Refresh the JWT this many seconds before its declared expiry to avoid
# sending a token that expires in transit.
_REFRESH_MARGIN = 30

# Hard fallback TTL when the JWT payload cannot be decoded.
_FALLBACK_TTL = 300.0

_PIPE_ACCEPT = (
    "multipart/mixed;deferSpec=20220824, "
    "application/graphql-response+json, "
    "application/json"
)


def _jwt_exp_as_monotonic(token: str) -> float | None:
    """Return the ``exp`` claim of *token* as a :func:`time.monotonic` timestamp.

    Returns None when the token is malformed or lacks an ``exp`` claim, so the
    caller can fall back to a fixed TTL rather than crashing.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        # Base64url padding
        rem = len(payload_b64) % 4
        if rem:
            payload_b64 += "=" * (4 - rem)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp_unix = payload.get("exp")
        if exp_unix is None:
            return None
        # Convert Unix epoch → monotonic by anchoring on the current instant.
        return time.monotonic() + (float(exp_unix) - time.time())
    except Exception:
        return None


class DeezerPipeClient:
    """Thin async client for the Deezer Pipe GraphQL API.

    Acquires and transparently renews the JWT so callers just call
    :meth:`query` without caring about token lifecycle.

    The JWT has a 6-minute TTL.  This client proactively refreshes it
    ``_REFRESH_MARGIN`` seconds before expiry and retries once on HTTP 401
    (server-side expiry that slipped past the margin).

    Args:
        arl: Deezer ARL cookie value used as the refresh token.
        session: Shared :class:`aiohttp.ClientSession` (must be open).
    """

    def __init__(self, arl: str, session: aiohttp.ClientSession) -> None:
        self._arl = arl
        self._session = session
        self._jwt: str | None = None
        self._jwt_expires_at: float = 0.0  # monotonic
        self._lock = asyncio.Lock()

    # ── JWT lifecycle ─────────────────────────────────────────────────────────

    async def _refresh_jwt(self) -> None:
        """Exchange the ARL for a fresh JWT; update internal state."""
        async with self._session.post(
            _RENEW_URL,
            params={"i": "p", "jo": "p", "rto": "p"},
            json={"refresh_token": self._arl},
            headers={"Content-Type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        jwt = data.get("jwt")
        if not jwt:
            raise RuntimeError(
                f"auth.deezer.com/login/renew returned no JWT (keys: {list(data)})"
            )

        exp = _jwt_exp_as_monotonic(jwt)
        if exp is None:
            exp = time.monotonic() + _FALLBACK_TTL
            logger.debug(
                "Deezer Pipe: JWT exp not decodable, using %.0fs fallback TTL",
                _FALLBACK_TTL,
            )

        self._jwt = jwt
        self._jwt_expires_at = exp
        ttl = exp - time.monotonic()
        # Log only a short prefix so the token never appears in full in logs.
        logger.debug("Deezer Pipe JWT acquired (…%s), TTL %.0fs", jwt[-8:], ttl)

    async def _get_jwt(self) -> str:
        """Return a valid JWT, refreshing proactively if near expiry.

        Uses a lock to ensure only one refresh runs at a time even when many
        coroutines race to detect the expired state.
        """
        if self._jwt and time.monotonic() < self._jwt_expires_at - _REFRESH_MARGIN:
            return self._jwt
        async with self._lock:
            # Re-check after acquiring the lock — another coroutine may have
            # already refreshed while this one was waiting.
            if self._jwt and time.monotonic() < self._jwt_expires_at - _REFRESH_MARGIN:
                return self._jwt
            await self._refresh_jwt()
            return self._jwt  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Force a JWT refresh on the next :meth:`_get_jwt` call."""
        self._jwt_expires_at = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    async def query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the Pipe API.

        Retries once on HTTP 401, forcing a JWT refresh between attempts.

        Args:
            query: GraphQL query or mutation string.
            variables: Optional variables dict.

        Returns:
            Parsed JSON response body.

        Raises:
            aiohttp.ClientResponseError: On HTTP errors other than 401-then-retry.
            RuntimeError: If a 401 persists after JWT refresh.
        """
        for attempt in range(2):
            jwt = await self._get_jwt()
            async with self._session.post(
                _PIPE_URL,
                json={"query": query, "variables": variables or {}},
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Content-Type": "application/json",
                    "Accept": _PIPE_ACCEPT,
                },
            ) as resp:
                if resp.status == 401 and attempt == 0:
                    logger.debug("Deezer Pipe: 401 on attempt 0, forcing JWT refresh")
                    self.invalidate()
                    continue
                resp.raise_for_status()
                return await resp.json()

        raise RuntimeError("Deezer Pipe API returned 401 after JWT refresh")
