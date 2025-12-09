"""
Registro centralizado de blueprints da aplicacao.

Este modulo e responsavel por importar e registrar todos os blueprints
da aplicacao Flask, organizando as rotas por dominio de negocio.

Blueprints Disponiveis:
    - health_bp: Health checks e status
    - uploads_bp: Upload de arquivos
    - tags_bp: Gestao de tags
    - procedimentos_bp: Procedimentos operacionais
    - acessos_bp: Central de acessos
    - auth_bp: Autenticacao
    - cursos_bp: Catalogo de cursos
    - consultorias_bp: Gestao de consultorias
    - calendario_bp: Calendario de colaboradores
    - diretoria_bp: Gestao da diretoria
    - notifications_bp: Notificacoes e SSE
    - notas_bp: Notas de debito
    - reunioes_bp: Sala de reunioes
    - relatorios_bp: Relatorios administrativos
    - users_bp: Gestao de usuarios
    - tasks_bp: Gestao de tarefas
    - empresas_bp: Gestao de empresas

Uso:
    from app.controllers.routes.blueprints import register_all_blueprints
    register_all_blueprints(app)

Autor: Refatoracao automatizada
Data: 2024
"""

from flask import Flask


def register_all_blueprints(app: Flask) -> None:
    """
    Registra todos os blueprints na aplicacao Flask.

    Os blueprints sao registrados sem url_prefix para manter
    compatibilidade com templates existentes que usam url_for().

    NOTA: Blueprints com rotas implementadas sao registrados aqui.
    Blueprints vazios (placeholders) nao sao registrados para evitar conflitos.
    As rotas correspondentes permanecem em __init__.py ate migracao completa.

    Args:
        app: Instancia da aplicacao Flask.
    """
    # =========================================================================
    # BLUEPRINTS COM ROTAS IMPLEMENTADAS
    # Estes blueprints tem rotas funcionais e podem ser registrados
    # =========================================================================

    # Health - endpoints de infraestrutura e PWA (/ping, /offline, /sw.js)
    from app.controllers.routes.blueprints.health import health_bp
    app.register_blueprint(health_bp)

    # Uploads - upload de imagens e arquivos (/upload_image, /upload_file)
    from app.controllers.routes.blueprints.uploads import uploads_bp
    app.register_blueprint(uploads_bp)

    # Tags - gestao de tags (/tags, /tags/cadastro, /tags/editar/<id>)
    from app.controllers.routes.blueprints.tags import tags_bp
    app.register_blueprint(tags_bp)

    # Procedimentos - procedimentos operacionais (/procedimentos/*)
    from app.controllers.routes.blueprints.procedimentos import procedimentos_bp
    app.register_blueprint(procedimentos_bp)

    # Acessos - central de acessos/links (/acessos/*)
    from app.controllers.routes.blueprints.acessos import acessos_bp
    app.register_blueprint(acessos_bp)

    # Auth - login, logout, OAuth (/login, /logout, /oauth2callback, /cookies)
    from app.controllers.routes.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)

    # Cursos - catalogo de cursos (/cursos)
    from app.controllers.routes.blueprints.cursos import cursos_bp
    app.register_blueprint(cursos_bp)

    # Consultorias - gestao de consultorias e setores
    from app.controllers.routes.blueprints.consultorias import consultorias_bp
    app.register_blueprint(consultorias_bp)

    # =========================================================================
    # BLUEPRINTS PLACEHOLDER (rotas ainda em __init__.py)
    # Descomentar conforme as rotas forem migradas para cada blueprint
    # =========================================================================

    # Calendario - calendario de colaboradores
    # from app.controllers.routes.blueprints.calendario import calendario_bp
    # app.register_blueprint(calendario_bp)

    # Diretoria - acordos, feedbacks, eventos
    # from app.controllers.routes.blueprints.diretoria import diretoria_bp
    # app.register_blueprint(diretoria_bp)

    # Notifications - notificacoes e SSE
    # from app.controllers.routes.blueprints.notifications import notifications_bp
    # app.register_blueprint(notifications_bp)

    # Notas - notas de debito
    # from app.controllers.routes.blueprints.notas import notas_bp
    # app.register_blueprint(notas_bp)

    # Reunioes - sala de reunioes
    # from app.controllers.routes.blueprints.reunioes import reunioes_bp
    # app.register_blueprint(reunioes_bp)

    # Relatorios - relatorios administrativos
    # from app.controllers.routes.blueprints.relatorios import relatorios_bp
    # app.register_blueprint(relatorios_bp)

    # Users - gestao de usuarios
    # from app.controllers.routes.blueprints.users import users_bp
    # app.register_blueprint(users_bp)

    # Tasks - gestao de tarefas
    # from app.controllers.routes.blueprints.tasks import tasks_bp
    # app.register_blueprint(tasks_bp)

    # Empresas - gestao de empresas
    # from app.controllers.routes.blueprints.empresas import empresas_bp
    # app.register_blueprint(empresas_bp)

    # Adiciona aliases legados para manter compatibilidade com templates
    _add_legacy_endpoint_aliases(app)


def _add_legacy_endpoint_aliases(app: Flask) -> None:
    """
    Adiciona aliases de endpoints legados para compatibilidade.

    Permite que templates usando url_for('funcao') continuem
    funcionando apos migracao para blueprints.

    Args:
        app: Instancia da aplicacao Flask.
    """
    # Lista de blueprints que precisam de aliases
    blueprint_prefixes = [
        'health.', 'uploads.', 'tags.', 'procedimentos.', 'acessos.',
        'auth.', 'cursos.', 'consultorias.', 'calendario.',
        'diretoria.', 'notifications.', 'notas.',
        'reunioes.', 'relatorios.', 'users.',
        'tasks.', 'empresas.'
    ]

    for rule in list(app.url_map.iter_rules()):
        # Verifica se e um endpoint de blueprint
        endpoint_prefix = None
        for prefix in blueprint_prefixes:
            if rule.endpoint.startswith(prefix):
                endpoint_prefix = prefix
                break

        if not endpoint_prefix:
            continue

        # Extrai nome do endpoint sem prefixo do blueprint
        legacy_endpoint = rule.endpoint.split(".", 1)[1]

        # Pula se endpoint legado ja existe
        if legacy_endpoint in app.view_functions:
            continue

        # Adiciona alias para compatibilidade
        view_func = app.view_functions[rule.endpoint]
        app.add_url_rule(
            rule.rule,
            endpoint=legacy_endpoint,
            view_func=view_func,
            defaults=rule.defaults,
            methods=rule.methods,
            provide_automatic_options=False,
        )
