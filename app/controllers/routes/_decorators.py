"""
Decorators de autenticacao e autorizacao para rotas.

Este modulo centraliza todos os decorators utilizados para controle de acesso
nas rotas da aplicacao, garantindo consistencia nas verificacoes de permissao.

Decorators Disponiveis:
    - admin_required: Restringe acesso a usuarios admin
    - report_access_required: Controle de acesso a relatorios
    - meeting_only_access_check: Bloqueia usuarios com acesso apenas a reunioes

Funcoes Auxiliares:
    - has_report_access: Verifica permissao de acesso a relatorio
    - is_meeting_only_user: Verifica se usuario tem acesso restrito
    - get_accessible_tag_ids: Retorna IDs de tags acessiveis pelo usuario

Autor: Refatoracao automatizada
Data: 2024
"""

from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user, login_required

from app.models.tables import ReportPermission, Tag, User
from app.utils.permissions import is_user_admin
from app.controllers.routes._base import PERSONAL_TAG_PREFIX, get_ti_tag


# =============================================================================
# FUNCOES AUXILIARES
# =============================================================================

def _get_report_permissions_for_code(report_code: str) -> list[ReportPermission]:
    """
    Retorna permissoes armazenadas para um codigo de relatorio/portal.

    Args:
        report_code: Codigo identificador do relatorio.

    Returns:
        list[ReportPermission]: Lista de permissoes configuradas.
    """
    return (
        ReportPermission.query.filter(
            ReportPermission.report_code == report_code
        ).all()
    )


def get_accessible_tag_ids(user: User | None = None) -> list[int]:
    """
    Retorna IDs das tags que o usuario pode acessar.

    Inclui tags diretas do usuario, tag pessoal e tag TI se aplicavel.

    Args:
        user: Usuario para verificar (default: current_user).

    Returns:
        list[int]: Lista de IDs de tags acessiveis.
    """
    if user is None:
        user = current_user if current_user.is_authenticated else None
    if user is None:
        return []

    ids = {t.id for t in getattr(user, "tags", []) or []}

    if getattr(user, "role", None) == "admin":
        return list(ids)

    # Inclui tag pessoal do usuario
    personal_tag_name = f"{PERSONAL_TAG_PREFIX}{user.id}"
    personal_tag = Tag.query.filter_by(nome=personal_tag_name).first()
    if personal_tag:
        ids.add(personal_tag.id)

    # Usuarios TI podem ver tarefas TI
    ti_tag = get_ti_tag()
    if ti_tag and ti_tag.id in ids:
        # Usuario ja tem acesso TI
        pass

    return list(ids)


def has_report_access(report_code: str | None = None) -> bool:
    """
    Verifica se usuario atual pode acessar o relatorio especificado.

    Regras (em ordem):
    1. Admin ou master sempre tem acesso
    2. Se existem permissoes configuradas, verifica tags/usuarios permitidos
    3. Caso contrario, usa tags legadas (Administrativo, Relatorios, etc.)

    Args:
        report_code: Codigo do relatorio ou None para verificacao de menu.

    Returns:
        bool: True se usuario tem acesso.
    """
    if current_user.role == "admin" or getattr(current_user, "is_master", False):
        return True
    if not current_user.is_authenticated:
        return False

    user_tag_ids = set(get_accessible_tag_ids(current_user))

    if report_code is None:
        # Verificacao de menu: permite se usuario tem qualquer permissao
        any_permissions = ReportPermission.query.all()
        if any_permissions:
            for permission in any_permissions:
                if permission.user_id == current_user.id:
                    return True
                if permission.tag_id and permission.tag_id in user_tag_ids:
                    return True
            return False
        # Sem permissoes configuradas, usa tags legadas
        return any(
            (tag.nome or "").lower() in {"relatorios", "relatórios"}
            for tag in current_user.tags
        )

    code = report_code or "index"
    stored_permissions = _get_report_permissions_for_code(code)

    if stored_permissions:
        for permission in stored_permissions:
            if permission.user_id == current_user.id:
                return True
            if permission.tag_id and permission.tag_id in user_tag_ids:
                return True
        return False

    # Fallback para tags legadas
    allowed_tags = {"relatorios", "relatórios"}
    if code:
        allowed_tags.add(f"relatórios:{code}".lower())
        allowed_tags.add(f"relatorios:{code}".lower())
    return any((tag.nome or "").lower() in allowed_tags for tag in current_user.tags)


def has_portal_permission(permission_code: str) -> bool:
    """
    Verifica se o usuario tem permissao explicita para uma acao do portal.

    - Admin/master: sempre permitido
    - Caso exista alguma permissao cadastrada para o codigo, verifica tags/usuarios
    - Se nao houver permissoes salvas, nega (opt-in)
    """
    if current_user.role == "admin" or getattr(current_user, "is_master", False):
        return True
    if not current_user.is_authenticated:
        return False

    user_tag_ids = set(get_accessible_tag_ids(current_user))
    stored_permissions = _get_report_permissions_for_code(permission_code)
    if not stored_permissions:
        return False

    for permission in stored_permissions:
        if permission.user_id == current_user.id:
            return True
        if permission.tag_id and permission.tag_id in user_tag_ids:
            return True
    return False


def is_meeting_only_user() -> bool:
    """
    Verifica se usuario atual tem acesso apenas a Sala de Reunioes.

    Um usuario e considerado "meeting-only" se:
    - Esta autenticado
    - Nao e admin
    - Possui apenas a tag 'reuniao'

    Returns:
        bool: True se usuario tem acesso restrito a reunioes.
    """
    if not current_user.is_authenticated:
        return False

    # Admins nao sao meeting-only
    if is_user_admin(current_user):
        return False

    user_tags = getattr(current_user, 'tags', []) or []
    if not user_tags:
        return False

    # Verifica se tem a tag reuniao
    has_reunion_tag = any(tag.nome.lower() == 'reunião' for tag in user_tags)

    if not has_reunion_tag:
        return False

    # Se tem apenas a tag reuniao, e meeting-only
    if len(user_tags) == 1:
        return True

    # Se tem outras tags alem de reuniao, NAO e meeting-only
    return False


# =============================================================================
# DECORATORS
# =============================================================================

def admin_required(f):
    """
    Decorator que restringe acesso a usuarios admin.

    Uso:
        @app.route('/admin')
        @admin_required
        def admin_page():
            ...

    Raises:
        403: Se usuario nao for admin.
    """
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


def report_access_required(report_code: str | None = None):
    """
    Decorator que permite acesso a admin ou usuarios com tags apropriadas.

    Args:
        report_code: Codigo do relatorio para verificacao especifica.

    Uso:
        @app.route('/relatorios/empresas')
        @report_access_required('empresas')
        def relatorio_empresas():
            ...

    Raises:
        403: Se usuario nao tem permissao.
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if has_report_access(report_code):
                return f(*args, **kwargs)
            abort(403)

        return decorated_function

    return decorator


def meeting_only_access_check(f):
    """
    Decorator que bloqueia usuarios meeting-only em rotas nao permitidas.

    Usuarios com apenas a tag 'reuniao' sao redirecionados para
    a Sala de Reunioes se tentarem acessar outras paginas.

    Uso:
        @app.route('/dashboard')
        @meeting_only_access_check
        def dashboard():
            ...
    """
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if is_meeting_only_user():
            flash('Voce so tem acesso a Sala de Reunioes.', 'warning')
            return redirect(url_for('sala_reunioes'))
        return f(*args, **kwargs)

    return decorated_function


def require_master_admin(f):
    """
    Decorator que restringe acesso a usuarios admin ou master.

    Raises:
        403: Se usuario nao for admin ou master.
    """
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != "admin" and not getattr(current_user, "is_master", False):
            abort(403)
        return f(*args, **kwargs)

    return decorated_function
