import re
import subprocess

from typing import Sequence

# Regular expressions to strip potentially dangerous content
_SCRIPT_TAG_RE = re.compile(r'<script.*?>.*?</script>', re.IGNORECASE | re.DOTALL)
_EVENT_ATTR_RE = re.compile(r'on\w+\s*=\s*".*?"', re.IGNORECASE)
_JS_PROTOCOL_RE = re.compile(r'javascript:', re.IGNORECASE)


def sanitize_html(value: str | None) -> str:
    """Remove potentially dangerous HTML before rendering.

    The sanitizer removes script tags, inline event handlers and
    ``javascript:`` URLs, returning a cleaned HTML fragment.
    """
    if not value:
        return ""

    # Strip script tags completely
    cleaned = _SCRIPT_TAG_RE.sub("", value)
    # Drop inline event handler attributes like onclick="..."
    cleaned = _EVENT_ATTR_RE.sub("", cleaned)
    # Remove javascript: protocol usages
    cleaned = _JS_PROTOCOL_RE.sub("", cleaned)
    return cleaned


def run_command_safe(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    """Execute an external command securely.

    The command is run without a shell and arguments containing shell
    metacharacters are rejected to mitigate command injection attempts.
    """
    if not isinstance(cmd, Sequence) or not cmd:
        raise ValueError("cmd must be a non-empty sequence of strings")

    for arg in cmd:
        if not isinstance(arg, str):
            raise ValueError("command arguments must be strings")
        if any(c in arg for c in [';', '&', '|']):
            raise ValueError("unsafe characters in command argument")

    return subprocess.run(cmd, check=True, capture_output=True, text=True)


