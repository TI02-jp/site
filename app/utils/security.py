"""Security-related helper utilities.

Este módulo fornece sanitização HTML robusta usando a biblioteca bleach
para proteger contra ataques XSS (Cross-Site Scripting).
"""

import bleach
from bleach.css_sanitizer import CSSSanitizer

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
                lambda attrs, _new: {**attrs, **{(None, 'target'): '_blank', (None, 'rel'): 'noopener noreferrer'}}
            ],
            skip_tags=['pre', 'code']  # Não linkificar dentro de code blocks
        )

    return cleaned
