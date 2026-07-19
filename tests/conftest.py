"""Global pytest configuration and shared fixtures."""

import re

import pytest

# Google API key pattern — matches keys embedded in Deezer's getUserData response
# (thirdParty.googleplus.client_key).  These are Deezer's own public app credentials,
# not user secrets, but they trigger secret-scanning rules in CI.
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z\-_]{35}")

# Session tokens and personal data embedded in the getUserData GW response body.
# None of these are load-bearing for VCR replay (matching is on method + URI, and
# these values never appear in a request), so scrubbing them cannot break tests.
# The account id (USER_ID) and Deezer's per-session checkForm — which *is* echoed
# back as the api_token query param on subsequent requests — are deliberately left
# intact so cassettes keep replaying.
_BODY_SUBS = [
    # Personal data
    (r'"EMAIL":"[^"]*"', '"EMAIL":"redacted@example.com"'),
    (r'"FIRSTNAME":"[^"]*"', '"FIRSTNAME":"Redacted"'),
    (r'"LASTNAME":"[^"]*"', '"LASTNAME":"Redacted"'),
    (r'"BLOG_NAME":"[^"]*"', '"BLOG_NAME":"redacted"'),
    (r'"USER_GENDER":"[^"]*"', '"USER_GENDER":""'),
    (r'"USER_AGE":"[^"]*"', '"USER_AGE":""'),
    (r'"USER_PICTURE":"[^"]*"', '"USER_PICTURE":""'),
    (r'"location":\{[^}]*\}', '"location":{"city":"","lat":0,"lon":0,"source":"ip"}'),
    # Session tokens / credentials
    (r'"SESSION_ID":"[^"]*"', '"SESSION_ID":"<redacted>"'),
    (r'"USER_TOKEN":"[^"]*"', '"USER_TOKEN":"<redacted>"'),
    (r'"PLAYER_TOKEN":"[^"]*"', '"PLAYER_TOKEN":"<redacted>"'),
    (r'"license_token":"[^"]*"', '"license_token":"<redacted>"'),
    (r'"LASTFM":\{"TOKEN":"[^"]*"\}', '"LASTFM":{"TOKEN":"<redacted>"}'),
    (r'"apiKey":"[^"]*"', '"apiKey":"<redacted>"'),
    # Advertising / device identifiers (personal data under GDPR)
    (r'("featurefm_token":\{"token":")[^"]*"', r'\1<redacted>"'),
]
_BODY_SUBS_COMPILED = [(re.compile(p), r) for p, r in _BODY_SUBS]

# Google Advertising IDs (gps_adid), which appear as the object keys of
# `devicesInfo`. Each is replaced with a distinct zeroed UUID so the surrounding
# JSON object keeps unique keys.
_GPS_ADID_RE = re.compile(
    r'"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"'
    r'(:\{"identifier_type":"gps_adid"\})'
)


def scrub_text(text: str) -> str:
    """Redact session tokens, personal data, and Deezer's Google key from *text*.

    Operates on the JSON response body as raw text (rather than round-tripping
    through json) so only the targeted values change and everything else stays
    byte-for-byte identical. Reused by the cassette-sanitising pass so committed
    cassettes match exactly what a fresh recording would now produce.
    """
    text = _GOOGLE_KEY_RE.sub("<redacted>", text)
    for pattern, repl in _BODY_SUBS_COMPILED:
        text = pattern.sub(repl, text)

    counter = iter(range(1, 1000))
    text = _GPS_ADID_RE.sub(
        lambda m: f'"00000000-0000-0000-0000-{next(counter):012d}"{m.group(1)}',
        text,
    )
    return text


def _scrub_response(response: dict) -> dict:
    """Remove credentials and personal data from recorded responses before saving.

    Handles three cases:
    - Set-Cookie response headers: short-lived session tokens set by the API.
    - Session tokens in getUserData bodies (SESSION_ID, USER_TOKEN, PLAYER_TOKEN,
      license_token, Last.fm token, Braze apiKey).
    - Personal data in getUserData bodies (email, name, gender, age, avatar,
      geolocation) plus Deezer's own embedded Google Sign-In key.
    """
    headers = response.get("headers", {})
    for key in list(headers):
        if key.lower() == "set-cookie":
            headers[key] = ["<redacted>"]

    body = response.get("body", {})
    raw = body.get("string", b"")
    if isinstance(raw, bytes):
        body["string"] = scrub_text(raw.decode("utf-8", "surrogatepass")).encode(
            "utf-8", "surrogatepass"
        )
    elif isinstance(raw, str):
        body["string"] = scrub_text(raw)

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
