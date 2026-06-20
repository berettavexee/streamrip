"""Global pytest configuration and shared fixtures."""

import re

import pytest


def _scrub_response(response: dict) -> dict:
    """Remove session cookies from recorded responses before saving to cassette.

    filter_headers strips *request* Cookie headers (which contain the ARL).
    This callback handles the *response* side: Set-Cookie headers set short-lived
    session tokens (sid, dzr_uniq_id) that are harmless once expired, but
    scrubbing them keeps cassettes clean and avoids false security alarms in
    automated scanners.
    """
    headers = response.get("headers", {})
    for key in list(headers):
        if key.lower() == "set-cookie":
            headers[key] = ["<redacted>"]
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
