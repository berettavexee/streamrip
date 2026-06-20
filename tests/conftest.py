"""Global pytest configuration and shared fixtures."""

import re

import pytest

# Google API key pattern — matches keys embedded in Deezer's getUserData response
# (thirdParty.googleplus.client_key).  These are Deezer's own public app credentials,
# not user secrets, but they trigger secret-scanning rules in CI.
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z\-_]{35}")
_GOOGLE_KEY_RE_BYTES = re.compile(rb"AIza[0-9A-Za-z\-_]{35}")


def _scrub_response(response: dict) -> dict:
    """Remove credentials from recorded responses before saving to cassette.

    Handles two cases:
    - Set-Cookie response headers: short-lived session tokens set by the API.
    - Google API keys in response bodies: Deezer embeds its own Google Sign-In
      client_key in the getUserData GW response; strip it to avoid secret-scanner
      false positives in CI.
    """
    headers = response.get("headers", {})
    for key in list(headers):
        if key.lower() == "set-cookie":
            headers[key] = ["<redacted>"]

    body = response.get("body", {})
    raw = body.get("string", b"")
    if isinstance(raw, bytes):
        body["string"] = _GOOGLE_KEY_RE_BYTES.sub(b"<redacted>", raw)
    elif isinstance(raw, str):
        body["string"] = _GOOGLE_KEY_RE.sub("<redacted>", raw)

    return response


@pytest.fixture(scope="session")
def vcr_config():
    """Strip credentials and session tokens from VCR cassettes.

    filter_headers removes the Cookie request header (which carries the ARL)
    before any interaction is saved.  before_record_response removes Set-Cookie
    response headers (session tokens).

    decode_compressed_response stores responses as plain text rather than gzip
    bytes, making cassettes human-readable and portable across platforms.
    """
    return {
        "filter_headers": ["Cookie", "Authorization"],
        "before_record_response": _scrub_response,
        "decode_compressed_response": True,
    }
