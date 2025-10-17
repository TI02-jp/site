"""Security-related helper utilities."""

import re

# Regular expressions to strip potentially dangerous content
_SCRIPT_TAG_RE = re.compile(r'<script.*?>.*?</script>', re.IGNORECASE | re.DOTALL)
# Matches inline event handlers, e.g. onclick="..."
_EVENT_ATTR_RE = re.compile(r'on\w+\s*=\s*".*?"', re.IGNORECASE)
_JS_PROTOCOL_RE = re.compile(r'javascript:', re.IGNORECASE)
# Detect plain URLs that are not already part of an HTML attribute value
_BARE_URL_RE = re.compile(
    r'(?<!["\'=])(?P<url>(?:https?://|www\.)[^\s<]+)',
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = re.compile(r'([.,!?;:]+)$')


def _linkify(text: str) -> str:
    """Wrap bare URLs in clickable anchor tags."""

    def _replace(match: re.Match[str]) -> str:
        raw_url = match.group("url")
        before = match.string[: match.start()]
        last_anchor_open = before.lower().rfind("<a")
        last_anchor_close = before.lower().rfind("</a")
        if last_anchor_open != -1 and (last_anchor_close == -1 or last_anchor_close < last_anchor_open):
            # Already inside an anchor tag; leave untouched
            return raw_url

        # Remove trailing punctuation to avoid broken links
        trailing = ""
        punct_match = _TRAILING_PUNCTUATION.search(raw_url)
        if punct_match:
            trailing = punct_match.group(1)
            raw_url = raw_url[: -len(trailing)]

        href = raw_url if raw_url.lower().startswith(("http://", "https://")) else f"https://{raw_url}"
        return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{raw_url}</a>{trailing}'

    return _BARE_URL_RE.sub(_replace, text)


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
    # Convert bare URLs into clickable links
    cleaned = _linkify(cleaned)
    return cleaned
