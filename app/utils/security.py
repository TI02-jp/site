"""Security-related helper utilities.

Este módulo fornece sanitização HTML robusta usando a biblioteca bleach
para proteger contra ataques XSS (Cross-Site Scripting).
"""

import re
import bleach
from bleach.css_sanitizer import CSSSanitizer
from bleach.linkifier import LinkifyFilter
from markupsafe import Markup

# Tags HTML permitidas para conteúdo rico
ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'u', 'a', 'ul', 'ol', 'li',
    'h1', 'h2', 'h3', 'h4', 'blockquote', 'code', 'pre',
    'span', 'div', 'b', 'i', 'img'
]

# Atributos permitidos por tag
ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title', 'target', 'rel', 'style'],
    'img': ['src', 'alt', 'title', 'width', 'height', 'style'],
    'span': ['class', 'style'],
    'div': ['class', 'style'],
    'code': ['class', 'style'],
    '*': ['class', 'style']  # Permite class/estilo controlado em todas as tags
}

# Protocolos permitidos em URLs
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']
ALLOWED_PROTOCOLS_WITH_DATA = ALLOWED_PROTOCOLS + ['data']

ALLOWED_CSS_PROPERTIES = [
    'color',
    'background-color',
    'text-align',
    'text-decoration',
    'font-weight',
    'font-style',
    'font-size',
    'line-height',
]

CSS_SANITIZER = CSSSanitizer(allowed_css_properties=ALLOWED_CSS_PROPERTIES)

# Regex para detectar URLs bare (mantida para compatibilidade)
_BARE_URL_RE = re.compile(
    r'(?<!["\'=])(?P<url>(?:https?://|www\.)[^\s<]+)',
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = re.compile(r'([.,!?;:]+)$')


def _linkify(text: str) -> str:
    """Wrap bare URLs in clickable anchor tags.

    DEPRECATED: Use bleach's linkify filter instead.
    Mantido para compatibilidade com código existente.
    """
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


def _build_attribute_filter(allow_data_images: bool):
    """Return a bleach attribute filter honoring data image settings."""

    def _is_allowed_attribute(tag: str, name: str, value: str) -> bool:
        allowed_for_tag = (ALLOWED_ATTRIBUTES.get(tag, []) or []) + (ALLOWED_ATTRIBUTES.get('*', []) or [])
        if name not in allowed_for_tag:
            return False
        if name in ("href", "src"):
            if value is None:
                return False
            lowered = str(value).strip().lower()
            if lowered.startswith("data:"):
                if not allow_data_images:
                    return False
                if tag == "img" and name == "src":
                    return lowered.startswith("data:image/") and not lowered.startswith("data:image/svg")
                return False
        return True

    return _is_allowed_attribute


def sanitize_html(
    value: str | None,
    linkify: bool = True,
    strip: bool = True,
    allow_data_images: bool = False,
) -> str:
    """Remove potentially dangerous HTML and return safe HTML fragment.

    Esta função usa a biblioteca bleach (mantida pela Mozilla) para fornecer
    sanitização HTML robusta contra ataques XSS.

    Args:
        value: HTML a ser sanitizado
        linkify: Se True, converte URLs em links clicáveis
        strip: Se True, remove tags não permitidas completamente.
               Se False, escapa tags não permitidas.

    Returns:
        str: HTML sanitizado e seguro para renderização

    Examples:
        >>> sanitize_html('<script>alert("XSS")</script><p>Texto seguro</p>')
        '<p>Texto seguro</p>'

        >>> sanitize_html('<p onclick="alert()">Texto</p>')
        '<p>Texto</p>'

        >>> sanitize_html('<a href="javascript:alert()">Link</a>')
        '<a>Link</a>'

    Proteções implementadas:
        - Remove tags de script, style, object, embed
        - Remove atributos de eventos (onclick, onload, etc)
        - Remove protocolo javascript: de URLs
        - Permite apenas tags e atributos específicos (whitelist)
        - Permite apenas protocolos http/https/mailto
        - Opcionalmente converte URLs em links clicáveis
    """
    if not value:
        return ""

    # Sanitizar HTML com bleach
    attribute_filter = _build_attribute_filter(allow_data_images)
    protocols = ALLOWED_PROTOCOLS_WITH_DATA if allow_data_images else ALLOWED_PROTOCOLS
    cleaned = bleach.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=attribute_filter,
        protocols=protocols,
        strip=strip,  # Remove tags não permitidas (vs escapar)
        css_sanitizer=CSS_SANITIZER,
    )

    # Linkify URLs se solicitado
    if linkify:
        from html import unescape
        # Primeiro decodifica entidades HTML para evitar linkify duplo
        cleaned = unescape(cleaned)
        # Aplica linkify do bleach
        cleaned = bleach.linkify(
            cleaned,
            callbacks=[
                # Adiciona target=_blank e rel=noopener noreferrer
                lambda attrs, new: {**attrs, **{(None, 'target'): '_blank', (None, 'rel'): 'noopener noreferrer'}}
            ],
            skip_tags=['pre', 'code']  # Não linkificar dentro de code blocks
        )

    return cleaned


def escape_html(value: str | None) -> str:
    """Escapa completamente HTML para exibição como texto plano.

    Use esta função quando você quer exibir HTML literalmente,
    não permitindo nenhuma formatação HTML.

    Args:
        value: Texto a ser escapado

    Returns:
        str: Texto com caracteres HTML escapados

    Example:
        >>> escape_html('<script>alert("XSS")</script>')
        '&lt;script&gt;alert("XSS")&lt;/script&gt;'
    """
    if not value:
        return ""
    # bleach.clean com tags=[] e strip=False escapa todo HTML
    return bleach.clean(value, tags=[], attributes={}, strip=False)
