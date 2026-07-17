import re

_DEFAULT_PATTERNS = [
    r"(?i)(secret|password|passwd|token|api_?key|auth)\s*[=:]\s*['\"]?[^\s\"',]+['\"]?",
    # Header form: Authorization: Bearer <token>  /  Authorization: <token>
    r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?['\"]?[^\s\"',]+['\"]?",
    # Bare form: Bearer <token-shaped string>
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
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

    for pattern in _compiled_patterns():
        tb = pattern.sub(replacement, tb)

    return tb
