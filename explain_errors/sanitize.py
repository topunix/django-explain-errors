import re

# A quoted value is taken whole (it may contain spaces), to the closing quote
# or the end of the line if the quote is never closed. An unquoted one runs to
# the next whitespace, quote, or comma.
_VALUE = r"""(?:"[^"\n]*"?|'[^'\n]*'?|[^\s"',]+)"""

# Prefixes of well-known API key shapes, matched whatever the variable is
# called. Kept short on purpose: every entry is a fixed prefix plus a minimum
# run of token characters, so ordinary words do not match.
#   sk-            OpenAI (sk-proj-...) and Anthropic (sk-ant-...)
#   sk_live_ / sk_test_   Stripe secret keys
#   ghp_ gho_ ghu_ ghs_ ghr_ / github_pat_   GitHub tokens
#   xoxb- xoxa- xoxp- xoxr- xoxs-   Slack tokens
#   AKIA           AWS access key IDs
#   AIza           Google API keys
_KEY_SHAPES = (
    r"sk-[A-Za-z0-9_-]{20,}",
    r"sk_(?:live|test)_[A-Za-z0-9]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_-]{35}",
)

# Well-known Django settings whose names start with a keyword but whose
# values are not secrets. They are matched first, as a whole name, and left
# untouched. Exact names only (case-sensitive): AUTH_USER_MODEL_X is not
# covered. Keep this list short; anything else starting with a keyword is
# still redacted (token_count, PASSWORD_FIELD, ...).
_SAFE_SETTING_NAMES = (
    "AUTH_USER_MODEL",
    "AUTHENTICATION_BACKENDS",
    "AUTH_PASSWORD_VALIDATORS",
    "PASSWORD_HASHERS",
    "PASSWORD_RESET_TIMEOUT",
)

_DEFAULT_PATTERNS = [
    # Allowlisted names (group "safe") come first so the keyword rule below
    # cannot start matching inside them, e.g. at PASSWORD in
    # AUTH_PASSWORD_VALIDATORS. sanitize_traceback keeps these as they are.
    r"(?P<safe>\b(?:" + "|".join(_SAFE_SETTING_NAMES) + r")\b)|"
    # Keyword, then an optional continuation of the name (SECRET_KEY,
    # AWS_SECRET_ACCESS_KEY, secret-key, apiKeyId), an optional closing quote
    # (a dict key like 'PASSWORD'), then = or : and the value. The
    # continuation must start with _ or - or be capitalized, so names that
    # merely begin with a keyword (author, tokenizer, tokens) are left alone,
    # and a bare "key" is deliberately not a keyword.
    r"(?i:secret|password|passwd|token|api_?key|auth)(?:[_-]\w*|(?-i:[A-Z]\w*))?"
    r"['\"]?\s*[=:]\s*" + _VALUE,
    # Header form: Authorization: Bearer <token>  /  Authorization: <token>
    r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?['\"]?[^\s\"',]+['\"]?",
    # Bare form: Bearer <token-shaped string>
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
    # Credentials in a URL: scheme://user:pw@host redacts "user:pw". Must run
    # before the email pattern, which would otherwise match "pw@host.tld".
    # Edge case: a custom EXPLAIN_ERRORS_REDACT_REPLACEMENT without brackets
    # (e.g. "***") can then match the email pattern as "***@host" and redact
    # the host too.
    r"(?<=://)[^\s:/@'\"]+:[^\s/@'\"]*(?=@)",
    # API key shapes, regardless of variable name.
    r"(?<![A-Za-z0-9])(?:" + "|".join(_KEY_SHAPES) + r")",
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
]



def _compiled_patterns():
    from django.conf import settings

    patterns = []

    if not getattr(settings, "EXPLAIN_ERRORS_REDACT_DISABLE_DEFAULTS", False):
        patterns.extend(re.compile(p) for p in _DEFAULT_PATTERNS)

    for raw in getattr(settings, "EXPLAIN_ERRORS_REDACT_PATTERNS", []):
        try:
            patterns.append(re.compile(raw))
        except re.error as exc:
            print(f"explain_errors: skipping invalid redaction pattern {raw!r}: {exc}")

    return patterns


def sanitize_traceback(tb: str) -> str:
    """Redact secrets and PII from a traceback string.

    INVARIANT: every string sent to an external service or written to a
    persistent index (future RAG layer) MUST pass through this function first.
    """
    from django.conf import settings

    replacement = getattr(settings, "EXPLAIN_ERRORS_REDACT_REPLACEMENT", "[REDACTED]")

    def redact(match):
        if match.groupdict().get("safe"):
            return match.group(0)
        return match.expand(replacement)

    for pattern in _compiled_patterns():
        tb = pattern.sub(redact, tb)

    return tb
