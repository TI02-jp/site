"""
Constantes centralizadas da aplicação.

Este módulo centraliza constantes utilizadas em toda a aplicação,
evitando magic strings e facilitando manutenção.

Seções:
    - UPLOAD: Extensões e tipos de arquivos permitidos
    - CATEGORIES: Categorias de acessos e eventos
    - REPORTS: Definições de relatórios
    - TAGS: Prefixos e exclusões de tags
    - OAUTH: Escopos de autenticação Google
    - CACHE: Prefixos de chaves de cache
    - PAGINATION: Configurações de paginação
"""

from typing import Any


# =============================================================================
# UPLOAD - EXTENSÕES E TIPOS DE ARQUIVOS
# =============================================================================

# Extensões de imagem permitidas (sem ponto)
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

# Extensões de imagem com ponto (para verificação de path)
IMAGE_EXTENSIONS_WITH_DOT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# Extensões permitidas para uploads (imagens + PDFs)
ALLOWED_EXTENSIONS_WITH_PDF = IMAGE_EXTENSIONS | {"pdf"}

# MIME types de imagem permitidos
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif"}

# MIME types de PDF permitidos
PDF_MIME_TYPES = {"application/pdf"}

# Extensões de vídeo permitidas
VIDEO_EXTENSIONS = {"mp4", "webm"}

# MIME types de vídeo permitidos
VIDEO_MIME_TYPES = {"video/mp4", "video/webm"}

# Tamanho máximo de vídeo em MB
VIDEO_MAX_SIZE_MB = 1024  # 1 GB

# Extensões de arquivos de anúncio permitidas
ANNOUNCEMENT_FILE_EXTENSIONS = (
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg"
)

# Mapeamento de assinaturas de imagem para extensões
IMAGE_SIGNATURE_MAP = {
    "jpeg": {"jpg", "jpeg"},
    "png": {"png"},
    "gif": {"gif"},
}

# Assinaturas de arquivo de vídeo (magic bytes)
VIDEO_SIGNATURES = {
    b"\x00\x00\x00\x18ftypmp4": "mp4",
    b"\x00\x00\x00\x1Cftypiso": "mp4",
    b"\x00\x00\x00\x20ftypiso": "mp4",
    b"\x1A\x45\xDF\xA3": "webm",
}


# =============================================================================
# DIRETÓRIOS DE UPLOAD
# =============================================================================

TASKS_UPLOAD_SUBDIR = "uploads/tasks"
MANUAL_VIDEOS_SUBDIR = "uploads/manual/videos"
MANUAL_THUMBNAILS_SUBDIR = "uploads/manual/thumbnails"
ANNOUNCEMENTS_UPLOAD_SUBDIR = "uploads/announcements"
DIRETORIA_UPLOAD_SUBDIR = "uploads/diretoria"


# =============================================================================
# CATEGORIAS - ACESSOS E EVENTOS
# =============================================================================

# Categorias de acessos por departamento
ACESSOS_CATEGORIES: dict[str, dict[str, Any]] = {
    "fiscal": {
        "title": "Fiscal",
        "description": "Sistemas utilizados pela equipe fiscal para gestão de obrigações e documentos.",
        "icon": "bi bi-clipboard-data",
    },
    "contabil": {
        "title": "Contábil",
        "description": "Ferramentas que apoiam a rotina contábil e o envio de documentos.",
        "icon": "bi bi-journal-check",
    },
    "pessoal": {
        "title": "Pessoal",
        "description": "Portais e ferramentas de apoio às rotinas de Departamento Pessoal e RH.",
        "icon": "bi bi-people",
    },
}

# Labels para tipos de eventos da diretoria
EVENT_TYPE_LABELS = {
    "treinamento": "Treinamento",
    "data_comemorativa": "Data comemorativa",
    "evento": "Evento",
}

# Labels para audiência de eventos
EVENT_AUDIENCE_LABELS = {
    "interno": "Interno",
    "externo": "Externo",
    "ambos": "Ambos",
}

# Labels para categorias de serviços em eventos
EVENT_CATEGORY_LABELS = {
    "cafe": "Café da manhã",
    "almoco": "Almoço",
    "lanche": "Lanche",
    "outros": "Outros serviços",
}


# =============================================================================
# RELATÓRIOS
# =============================================================================

REPORT_DEFINITIONS: dict[str, dict[str, str]] = {
    "empresas": {
        "title": "Relatório de Empresas",
        "description": "Dados consolidados das empresas",
    },
    "fiscal": {
        "title": "Relatório Fiscal",
        "description": "Indicadores e obrigações fiscais",
    },
    "contabil": {
        "title": "Relatório Contábil",
        "description": "Visão contábil e controle de relatórios",
    },
    "usuarios": {
        "title": "Relatório de Usuários",
        "description": "Gestão e estatísticas de usuários",
    },
    "cursos": {
        "title": "Relatório de Cursos",
        "description": "Métricas do catálogo de treinamentos",
    },
    "tarefas": {
        "title": "Relatório de Tarefas",
        "description": "Painel de tarefas e indicadores",
    },
}


# =============================================================================
# TAGS
# =============================================================================

# Tags excluídas das tarefas
EXCLUDED_TASK_TAGS = ["Reuniao"]
EXCLUDED_TASK_TAGS_LOWER = frozenset(t.lower() for t in EXCLUDED_TASK_TAGS)

# Prefixo para tags pessoais
PERSONAL_TAG_PREFIX = "__personal__"


# =============================================================================
# OAUTH - ESCOPOS GOOGLE
# =============================================================================

GOOGLE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.addons.current.message.action",
    "https://www.googleapis.com/auth/gmail.addons.current.action.compose",
]


# =============================================================================
# CACHE - PREFIXOS DE CHAVES
# =============================================================================

CACHE_KEY_STATS_PREFIX = "portal:stats:"
CACHE_KEY_NOTIFICATION_COUNT_PREFIX = "portal:notifications:unread:"
CACHE_KEY_NOTIFICATION_VERSION = "portal:notifications:version"
CACHE_KEY_SESSION_THROTTLE_PREFIX = "session_throttle:"


# =============================================================================
# PAGINAÇÃO
# =============================================================================

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


# =============================================================================
# STATUS LABELS (para exibição em português)
# =============================================================================

TASK_STATUS_LABELS = {
    "pending": "Pendente",
    "in_progress": "Em andamento",
    "done": "Concluída",
}

TASK_PRIORITY_LABELS = {
    "low": "Baixa",
    "medium": "Média",
    "high": "Alta",
}

MEETING_STATUS_LABELS = {
    "AGENDADA": "Agendada",
    "EM_ANDAMENTO": "Em andamento",
    "REALIZADA": "Realizada",
    "ADIADA": "Adiada",
    "CANCELADA": "Cancelada",
}


# =============================================================================
# REGIME DE LANÇAMENTO
# =============================================================================

REGIME_LANCAMENTO_CHOICES = [
    ("Caixa", "Caixa"),
    ("Competência", "Competência"),
]


# =============================================================================
# FORMA DE PAGAMENTO - STATUS COLORS
# =============================================================================

FORMA_PAGAMENTO_STATUS_COLORS = {
    "DEBITAR": "status-debitar",
    "A VISTA": "status-a-vista",
    "SEM ACORDO": "status-sem-acordo",
    "TADEU H.": "status-tadeu",
    "CORTESIA": "status-cortesia",
    "OK - PAGO": "status-ok-pago",
}
