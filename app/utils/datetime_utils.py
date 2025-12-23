"""
Utilitários centralizados para manipulação de datas e horas.

Este módulo padroniza o tratamento de timestamps em toda a aplicação,
garantindo consistência entre diferentes modelos e serviços.

Padrões da aplicação:
    - Timezone principal: America/Sao_Paulo (SAO_PAULO_TZ)
    - Armazenamento no MySQL: DATETIME sem timezone (naive)
    - Exibição: Sempre em horário de São Paulo

Uso recomendado:
    # Em models
    created_at = db.Column(db.DateTime, default=now_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_naive, onupdate=now_naive)

    # Em código
    from app.utils.datetime_utils import now_naive, now_aware, to_sao_paulo
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Timezone principal da aplicação
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


def now_naive() -> datetime:
    """
    Retorna datetime atual em São Paulo, sem timezone (naive).

    Ideal para armazenamento em colunas DATETIME do MySQL,
    que não suportam timezone nativamente.

    Returns:
        datetime: Data/hora atual em São Paulo, sem tzinfo.

    Example:
        >>> created_at = db.Column(db.DateTime, default=now_naive)
    """
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)


def now_aware() -> datetime:
    """
    Retorna datetime atual em São Paulo, com timezone (aware).

    Útil para comparações e cálculos que precisam de precisão
    de timezone, ou para APIs que esperam datetimes aware.

    Returns:
        datetime: Data/hora atual em São Paulo, com tzinfo.

    Example:
        >>> if event_time > now_aware():
        ...     print("Evento futuro")
    """
    return datetime.now(SAO_PAULO_TZ)


def to_sao_paulo(dt: datetime | None) -> datetime | None:
    """
    Converte datetime para timezone de São Paulo.

    Aceita tanto datetimes aware quanto naive. Para naive,
    assume que já está em UTC.

    Args:
        dt: Datetime a ser convertido.

    Returns:
        datetime | None: Datetime em São Paulo timezone, ou None.

    Example:
        >>> utc_time = datetime.now(timezone.utc)
        >>> sp_time = to_sao_paulo(utc_time)
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SAO_PAULO_TZ)


def to_naive_sao_paulo(dt: datetime | None) -> datetime | None:
    """
    Converte datetime para naive em timezone de São Paulo.

    Útil para armazenamento no MySQL após conversão de timezone.

    Args:
        dt: Datetime a ser convertido.

    Returns:
        datetime | None: Datetime naive em São Paulo, ou None.
    """
    converted = to_sao_paulo(dt)
    if converted is None:
        return None
    return converted.replace(tzinfo=None)


def format_datetime_br(dt: datetime | None, include_time: bool = True) -> str:
    """
    Formata datetime para exibição no padrão brasileiro.

    Args:
        dt: Datetime a ser formatado.
        include_time: Se True, inclui hora no formato.

    Returns:
        str: Data formatada como "DD/MM/YYYY HH:MM" ou "DD/MM/YYYY".
    """
    if dt is None:
        return "—"

    if include_time:
        return dt.strftime("%d/%m/%Y %H:%M")
    return dt.strftime("%d/%m/%Y")


def format_date_br(dt: datetime | None) -> str:
    """
    Formata apenas a data no padrão brasileiro.

    Args:
        dt: Datetime ou date a ser formatado.

    Returns:
        str: Data formatada como "DD/MM/YYYY".
    """
    return format_datetime_br(dt, include_time=False)


def time_since(dt: datetime | None) -> str:
    """
    Retorna tempo decorrido desde datetime em formato legível.

    Args:
        dt: Datetime de referência.

    Returns:
        str: Tempo decorrido (ex: "5 minutos atrás", "2 horas atrás").
    """
    if dt is None:
        return "agora"

    now = now_naive()
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "agora"

    minutes = seconds // 60
    if minutes == 1:
        return "1 minuto atrás"
    if minutes < 60:
        return f"{minutes} minutos atrás"

    hours = minutes // 60
    if hours == 1:
        return "1 hora atrás"
    if hours < 24:
        return f"{hours} horas atrás"

    days = hours // 24
    if days == 1:
        return "ontem"
    if days < 7:
        return f"{days} dias atrás"

    weeks = days // 7
    if weeks == 1:
        return "1 semana atrás"
    if weeks < 4:
        return f"{weeks} semanas atrás"

    return format_date_br(dt)
