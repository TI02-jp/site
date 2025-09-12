from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from app import db
from app.models.mural import MuralTarefa, StatusTarefa, Prioridade
from app.models.tables import Setor
from app.controllers.routes import admin_required
from app.utils.permissions import user_pode_atuar_no_setor


mural_bp = Blueprint("mural", __name__, url_prefix="/mural")


@mural_bp.route("/")
@login_required
def index():
    setores = Setor.query.filter_by(mural_habilitado=True).order_by(Setor.nome).all()
    if current_user.role != "admin":
        meus = [s for s in setores if any(us.id == s.id for us in current_user.setores)]
        if not meus:
            flash("Você não possui setor com mural habilitado.", "warning")
            return render_template("mural/empty.html")
        return redirect(url_for("mural.board", slug=meus[0].slug))
    contagens = []
    for s in setores:
        q = MuralTarefa.query.filter_by(setor_id=s.id)
        contagens.append(
            {
                "setor": s,
                "pendentes": q.filter_by(status=StatusTarefa.PENDENTE).count(),
                "andamento": q.filter_by(status=StatusTarefa.ANDAMENTO).count(),
                "concluidas": q.filter_by(status=StatusTarefa.CONCLUIDA).count(),
                "bloqueadas": q.filter_by(status=StatusTarefa.BLOQUEADA).count(),
            }
        )
    return render_template("mural/index.html", contagens=contagens)


@mural_bp.route("/board/<slug>")
@login_required
def board(slug):
    setor = Setor.query.filter_by(slug=slug, mural_habilitado=True).first_or_404()
    if not user_pode_atuar_no_setor(setor.id):
        abort(403)

    status_filtro = request.args.get("status")
    query = MuralTarefa.query.filter_by(setor_id=setor.id)
    if status_filtro in {s.value for s in StatusTarefa}:
        query = query.filter_by(status=StatusTarefa(status_filtro))
    else:
        query = query.filter(MuralTarefa.status.in_([StatusTarefa.PENDENTE, StatusTarefa.ANDAMENTO]))
    tarefas = query.order_by(MuralTarefa.prioridade.desc(), MuralTarefa.created_at.desc()).all()

    return render_template("mural/board.html", setor=setor, tarefas=tarefas, status_filtro=status_filtro)


@mural_bp.route("/nova", methods=["GET", "POST"])
@login_required
@admin_required
def nova():
    if request.method == "POST":
        setor_id = int(request.form["setor_id"])
        setor = Setor.query.get_or_404(setor_id)
        titulo = request.form["titulo"].strip()
        descricao = request.form.get("descricao", "").strip()
        prioridade = Prioridade(request.form.get("prioridade", "media"))
        data_limite = request.form.get("data_limite") or None
        data_limite = datetime.strptime(data_limite, "%Y-%m-%d").date() if data_limite else None

        tarefa = MuralTarefa(
            setor_id=setor.id,
            titulo=titulo,
            descricao=descricao,
            prioridade=prioridade,
            data_limite=data_limite,
            criada_por_id=current_user.id,
        )
        db.session.add(tarefa)
        db.session.commit()
        flash("Tarefa criada no mural.", "success")
        return redirect(url_for("mural.board", slug=setor.slug))

    setores = Setor.query.filter_by(mural_habilitado=True).order_by(Setor.nome).all()
    return render_template("mural/nova.html", setores=setores)


@mural_bp.route("/tarefa/<int:tarefa_id>/toggle-andamento", methods=["POST"])
@login_required
def toggle_andamento(tarefa_id):
    tarefa = MuralTarefa.query.get_or_404(tarefa_id)
    if not user_pode_atuar_no_setor(tarefa.setor_id):
        abort(403)

    if tarefa.status == StatusTarefa.PENDENTE:
        tarefa.set_status(StatusTarefa.ANDAMENTO, current_user)
    elif tarefa.status == StatusTarefa.ANDAMENTO:
        if current_user.role != "admin":
            abort(403)
        tarefa.set_status(StatusTarefa.PENDENTE, current_user)
    db.session.commit()
    return jsonify({"ok": True, "novo_status": tarefa.status.value})


@mural_bp.route("/tarefa/<int:tarefa_id>/concluir", methods=["POST"])
@login_required
def concluir(tarefa_id):
    tarefa = MuralTarefa.query.get_or_404(tarefa_id)
    if not user_pode_atuar_no_setor(tarefa.setor_id):
        abort(403)
    tarefa.set_status(StatusTarefa.CONCLUIDA, current_user)
    db.session.commit()
    return jsonify({"ok": True, "novo_status": tarefa.status.value})


@mural_bp.route("/tarefa/<int:tarefa_id>/reabrir", methods=["POST"])
@login_required
@admin_required
def reabrir(tarefa_id):
    tarefa = MuralTarefa.query.get_or_404(tarefa_id)
    tarefa.set_status(StatusTarefa.PENDENTE, current_user)
    db.session.commit()
    return jsonify({"ok": True, "novo_status": tarefa.status.value})

