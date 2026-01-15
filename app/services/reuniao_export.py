"""Geração de PDF de reuniões com cabeçalho timbrado.

Usa o modelo DOTX fornecido em static/models/timbrado - retrato.dotx para
preservar o cabeçalho e converte o conteúdo das decisões (HTML) em DOCX/PDF.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from typing import Iterable
from pathlib import Path
from zipfile import ZipFile

from bs4 import BeautifulSoup
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx2pdf import convert as docx2pdf_convert
from flask import current_app
from fpdf import FPDF, HTMLMixin

from app.models.tables import ClienteReuniao, User


def _slugify(value: str) -> str:
    """Return a filesystem-friendly slug."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "reuniao"


def _get_temp_dir() -> Path:
    """Get a reliable temporary directory with proper permissions.
    
    For Windows services, ensures the temp directory is accessible
    and has proper write permissions.
    """
    # Try instance folder first (usually has proper permissions in production)
    instance_dir = Path(current_app.instance_path)
    if instance_dir.exists():
        temp_dir = instance_dir / "tmp"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            # Test write permission
            test_file = temp_dir / ".test_write"
            test_file.touch()
            test_file.unlink()
            return temp_dir
        except (PermissionError, OSError):
            pass
    
    # Fall back to system temp directory
    try:
        temp_root = Path(tempfile.gettempdir())
        # For Windows services, create a subdirectory in temp
        temp_dir = temp_root / "flask_reuniao_pdf"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir
    except (PermissionError, OSError):
        # Last resort: use current working directory
        return Path.cwd()


class _HTMLPDF(FPDF, HTMLMixin):
    """Minimal HTML-capable PDF used as a fallback when docx2pdf is unavailable."""


def _materialize_docx_from_template(template_path: Path) -> Path:
    """Convert .dotx template into a .docx so python-docx can open it."""
    temp_dir = _get_temp_dir()
    tmp_copy = Path(tempfile.mkstemp(suffix=".docx", dir=str(temp_dir))[1])
    
    try:
        with ZipFile(template_path, "r") as source, ZipFile(tmp_copy, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    data = data.replace(
                        b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
                        b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                    )
                target.writestr(item, data)
    except Exception as e:
        current_app.logger.error(f"Erro ao materializar template DOCX: {e}", exc_info=True)
        raise
    
    return tmp_copy


def _add_html_to_docx(doc: Document, html_content: str) -> None:
    """Converte HTML do Quill para elementos do python-docx preservando formatação."""
    soup = BeautifulSoup(html_content, "html.parser")

    def get_indent_level(element):
        """Extrai nível de indentação das classes Quill."""
        classes = element.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        for cls in classes:
            if cls.startswith("ql-indent-"):
                try:
                    return int(cls.replace("ql-indent-", ""))
                except ValueError:
                    pass
        return 0

    def get_alignment(element):
        """Extrai alinhamento das classes Quill."""
        classes = element.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        for cls in classes:
            if cls == "ql-align-center":
                return WD_ALIGN_PARAGRAPH.CENTER
            elif cls == "ql-align-right":
                return WD_ALIGN_PARAGRAPH.RIGHT
            elif cls == "ql-align-justify":
                return WD_ALIGN_PARAGRAPH.JUSTIFY
        return WD_ALIGN_PARAGRAPH.LEFT

    def add_formatted_text(paragraph, element):
        """Adiciona texto formatado (negrito, itálico, etc) ao parágrafo."""
        if isinstance(element, str):
            text = element
            if text.strip():
                paragraph.add_run(text)
            return

        # Processa o elemento HTML
        if element.name in ["strong", "b"]:
            for child in element.children:
                if isinstance(child, str):
                    run = paragraph.add_run(child)
                    run.bold = True
                else:
                    add_formatted_text(paragraph, child)
        elif element.name in ["em", "i"]:
            for child in element.children:
                if isinstance(child, str):
                    run = paragraph.add_run(child)
                    run.italic = True
                else:
                    add_formatted_text(paragraph, child)
        elif element.name == "u":
            for child in element.children:
                if isinstance(child, str):
                    run = paragraph.add_run(child)
                    run.underline = True
                else:
                    add_formatted_text(paragraph, child)
        elif element.name == "br":
            paragraph.add_run("\n")
        elif element.name == "span":
            # Spans podem ter estilos inline
            for child in element.children:
                add_formatted_text(paragraph, child)
        else:
            # Para outros elementos, extrai o texto
            text = element.get_text()
            if text.strip():
                paragraph.add_run(text)

    def process_list(list_element, is_ordered=False):
        """Processa listas <ul> ou <ol>."""
        list_items = list_element.find_all("li", recursive=False)
        for idx, li in enumerate(list_items):
            indent = get_indent_level(li)
            alignment = get_alignment(li)

            # Cria parágrafo com marcador
            p = doc.add_paragraph()
            p.alignment = alignment

            # Aplica indentação
            if indent > 0:
                p.paragraph_format.left_indent = Inches(0.25 * indent)

            # Adiciona marcador/número
            if is_ordered:
                prefix = f"{idx + 1}. "
            else:
                prefix = "• "

            p.add_run(prefix)

            # Processa conteúdo do item
            for child in li.children:
                if child.name in ["ul", "ol"]:
                    # Lista aninhada
                    process_list(child, child.name == "ol")
                else:
                    add_formatted_text(p, child)

    def process_element(element):
        """Processa um elemento HTML genérico."""
        if isinstance(element, str):
            text = element.strip()
            if text:
                doc.add_paragraph(text)
            return

        if element.name == "p":
            p = doc.add_paragraph()
            p.alignment = get_alignment(element)
            indent = get_indent_level(element)
            if indent > 0:
                p.paragraph_format.left_indent = Inches(0.25 * indent)

            for child in element.children:
                add_formatted_text(p, child)

        elif element.name == "ul":
            process_list(element, is_ordered=False)
        elif element.name == "ol":
            process_list(element, is_ordered=True)
        elif element.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            level = int(element.name[1])
            doc.add_heading(element.get_text(), level=level)
        elif element.name == "br":
            doc.add_paragraph()
        elif element.name in ["div", "section", "article"]:
            # Processa filhos de containers
            for child in element.children:
                process_element(child)
        else:
            # Para outros elementos, tenta extrair texto
            text = element.get_text().strip()
            if text:
                doc.add_paragraph(text)

    # Processa todos os elementos filhos do body ou do soup diretamente
    body = soup.find("body")
    root = body if body else soup

    for child in root.children:
        if hasattr(child, "name") or (isinstance(child, str) and child.strip()):
            process_element(child)


def _resolve_participantes_labels(participantes_raw: Iterable | None) -> list[str]:
    """Return all participant names (users + guests) without duplicates."""

    if not participantes_raw:
        return []

    user_ids: list[int] = []
    guest_names: list[str] = []

    def _add_guest(name: str) -> None:
        cleaned = (name or "").strip()
        if cleaned:
            guest_names.append(cleaned[:255])

    # First pass: separate user IDs and guest names
    for participante in participantes_raw:
        if isinstance(participante, dict):
            p_type = (participante.get("type") or participante.get("tipo") or participante.get("kind") or "").lower()
            if p_type == "guest":
                _add_guest(participante.get("name") or participante.get("nome") or participante.get("label") or "")
                continue
            pid = participante.get("id") or participante.get("user_id")
            if isinstance(pid, int):
                user_ids.append(pid)
                continue
            _add_guest(participante.get("name") or participante.get("label") or "")
        elif isinstance(participante, int):
            user_ids.append(participante)
        elif isinstance(participante, str):
            _add_guest(participante)

    # Build user lookup to resolve names
    user_lookup: dict[int, User] = {}
    if user_ids:
        usuarios = User.query.filter(User.id.in_(user_ids)).all()
        user_lookup = {usuario.id: usuario for usuario in usuarios}

    resolved: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        cleaned = (name or "").strip()
        if cleaned and cleaned not in seen:
            resolved.append(cleaned)
            seen.add(cleaned)

    # Add resolved users in the order they appeared
    for pid in user_ids:
        usuario = user_lookup.get(pid)
        nome_usuario = None
        if usuario is not None:
            nome_usuario = getattr(usuario, "name", None) or getattr(usuario, "username", None)
        _add(nome_usuario or f"Usuario #{pid}")

    # Add guests
    for nome in guest_names:
        _add(nome)

    return resolved


def export_reuniao_decisoes_pdf(reuniao: ClienteReuniao) -> tuple[bytes, str]:
    """Render decisions into the timbrado template and return PDF bytes + filename."""
    # Get template path with absolute resolution for service compatibility
    template_filename = "timbrado - retrato.dotx"
    template_path = Path(current_app.root_path) / "static" / "models" / template_filename
    
    # Verify template exists
    if not template_path.exists():
        current_app.logger.error(f"Template file not found: {template_path}")
        raise FileNotFoundError(f"Modelo timbrado não encontrado: {template_path}")
    
    # Ensure we have read permissions
    if not os.access(template_path, os.R_OK):
        current_app.logger.error(f"No read permission for template: {template_path}")
        raise PermissionError(f"Sem permissão de leitura no modelo: {template_path}")

    base_docx_path = _materialize_docx_from_template(template_path)
    doc = Document(str(base_docx_path))

    empresa_nome = getattr(reuniao.empresa, "nome_empresa", "") or "Empresa"
    data_str = reuniao.data.strftime("%d/%m/%Y") if getattr(reuniao, "data", None) else "Não informado"
    setor_nome = getattr(getattr(reuniao, "setor", None), "nome", None) or "Não informado"
    acompanhar_ate_str = reuniao.acompanhar_ate.strftime("%d/%m/%Y") if getattr(reuniao, "acompanhar_ate", None) else "Não informado"

    # ==== TÍTULO PRINCIPAL ====
    titulo = doc.add_heading("ATA DE REUNIÃO", level=1)
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in titulo.runs:
        run.font.size = Pt(16)
        run.font.color.rgb = RGBColor(31, 78, 120)  # Azul corporativo

    # Adiciona espaço após título
    doc.add_paragraph()

    # ==== SEÇÃO: IDENTIFICAÇÃO ====
    identificacao_heading = doc.add_heading("IDENTIFICAÇÃO", level=2)
    for run in identificacao_heading.runs:
        run.font.color.rgb = RGBColor(31, 78, 120)

    # Empresa
    p_empresa = doc.add_paragraph()
    p_empresa.add_run(empresa_nome).bold = True

    # Data
    p_data = doc.add_paragraph()
    p_data.add_run(f"Data: {data_str}")

    # Setor
    p_setor = doc.add_paragraph()
    p_setor.add_run(f"Setor: {setor_nome}")

    # Adiciona espaço entre seções
    doc.add_paragraph()

    # ==== SEÇÃO: PARTICIPANTES ====
    participantes_heading = doc.add_heading("PARTICIPANTES", level=2)
    for run in participantes_heading.runs:
        run.font.color.rgb = RGBColor(31, 78, 120)

    # Monta lista completa de participantes vindos do campo da reunião
    todos_participantes = []
    seen = set()

    # Apenas quem está no campo de participantes (usuários + convidados)
    for nome in _resolve_participantes_labels(reuniao.participantes):
        if nome not in seen:
            todos_participantes.append(nome)
            seen.add(nome)

    # Renderiza a lista
    if todos_participantes:
        for nome in todos_participantes:
            p = doc.add_paragraph()
            p.add_run(nome)
    else:
        doc.add_paragraph("Não informado.")

    # Adiciona espaço entre seções
    doc.add_paragraph()

    # ==== SEÇÃO: ASSUNTOS TRATADOS ====
    assuntos_heading = doc.add_heading("ASSUNTOS TRATADOS", level=2)
    for run in assuntos_heading.runs:
        run.font.color.rgb = RGBColor(31, 78, 120)

    decisoes_html = reuniao.decisoes or "<p>Sem assuntos registrados.</p>"

    # Usa a função que preserva a formatação do Quill
    _add_html_to_docx(doc, decisoes_html)

    # Initialize variables for cleanup in finally block
    pdf_bytes: bytes | None = None
    tmp_docx_path: Path | None = None
    tmp_pdf_path: Path | None = None
    co_initialized = False
    pythoncom_mod = None
    temp_dir: Path | None = None

    # Wrap all temp file operations in try-except to trigger fallback on permission errors
    try:
        temp_dir = _get_temp_dir()
        current_app.logger.debug(f"Using temp directory: {temp_dir}")

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False, dir=str(temp_dir)) as tmp_docx:
            doc.save(tmp_docx.name)
            tmp_docx_path = Path(tmp_docx.name)

        current_app.logger.debug(f"Created temp DOCX at: {tmp_docx_path}")

        try:
            import pythoncom as pythoncom_mod  # type: ignore

            pythoncom_mod.CoInitialize()
            co_initialized = True
        except Exception:
            # Se ja estiver inicializado ou indisponivel (ex.: Linux), seguimos
            pythoncom_mod = None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=str(temp_dir)) as tmp_pdf:
            tmp_pdf_path = Path(tmp_pdf.name)

        # docx2pdf gera PDF usando Word/LibreOffice disponiveis
        try:
            docx2pdf_convert(str(tmp_docx_path), str(tmp_pdf_path))
            pdf_bytes = tmp_pdf_path.read_bytes()
            current_app.logger.debug("PDF generated successfully using docx2pdf")
        except Exception as docx2pdf_error:
            current_app.logger.warning(
                f"docx2pdf falhou na geração de PDF (usando fallback): {docx2pdf_error}",
                exc_info=docx2pdf_error
            )
            raise
    except Exception as exc:
        # Log detailed error information for debugging production issues
        error_context = {
            "temp_dir": str(temp_dir) if temp_dir else "not_created",
            "tmp_docx_path": str(tmp_docx_path) if tmp_docx_path else "not_created",
            "error_type": type(exc).__name__
        }
        current_app.logger.warning(
            "Temp file operations or docx2pdf failed (context: %s); using fallback HTML->PDF renderer: %s",
            error_context, exc, exc_info=exc
        )

        # Use fallback PDF generation with robust error handling
        participantes_labels = todos_participantes or []
        try:
            pdf_bytes = _render_pdf_fallback(
                empresa_nome=empresa_nome,
                data_str=data_str,
                setor_nome=setor_nome,
                acompanhar_ate_str=acompanhar_ate_str,
                participantes=participantes_labels,
                decisoes_html=decisoes_html,
            )
            current_app.logger.info("PDF generated successfully using fallback renderer")
        except Exception as fallback_error:
            current_app.logger.error(
                "Fallback PDF renderer also failed: %s", fallback_error, exc_info=fallback_error
            )
            # Re-raise to trigger 500 error with proper logging
            raise RuntimeError(
                "Falha ao gerar PDF da reunião usando ambos os métodos (docx2pdf e fallback)"
            ) from fallback_error
    finally:
        if co_initialized and pythoncom_mod:
            try:
                pythoncom_mod.CoUninitialize()
            except Exception:
                pass
        try:
            tmp_docx_path.unlink(missing_ok=True)
        except Exception as e:
            current_app.logger.debug(f"Não foi possível remover arquivo temporário DOCX: {e}")
        try:
            base_docx_path.unlink(missing_ok=True)
        except Exception as e:
            current_app.logger.debug(f"Não foi possível remover arquivo de base DOCX: {e}")
        if tmp_pdf_path:
            try:
                tmp_pdf_path.unlink(missing_ok=True)
            except Exception as e:
                current_app.logger.debug(f"Não foi possível remover arquivo temporário PDF: {e}")
    
    if not pdf_bytes:
        raise RuntimeError("Falha ao gerar PDF da reunião.")

    date_for_name = (
        reuniao.data.strftime("%Y-%m-%d") if getattr(reuniao, "data", None) else datetime.now().date()
    )
    empresa_slug = _slugify(empresa_nome)
    filename = f"reuniao-{empresa_slug}-{date_for_name}.pdf"

    return pdf_bytes, filename


def _render_pdf_fallback(
    *,
    empresa_nome: str,
    data_str: str,
    setor_nome: str,
    acompanhar_ate_str: str,
    participantes: list[str],
    decisoes_html: str,
) -> bytes:
    """Generate a lightweight PDF using fpdf2 when docx2pdf/Word is unavailable."""

    pdf = _HTMLPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "ATA DE REUNIÃO", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 12)

    meta_html = (
        "<h3>Identificação</h3>"
        f"<p><b>Empresa:</b> {empresa_nome}<br>"
        f"<b>Data:</b> {data_str}<br>"
        f"<b>Setor:</b> {setor_nome}<br>"
        f"<b>Acompanhar até:</b> {acompanhar_ate_str}</p>"
    )
    pdf.write_html(meta_html)

    participantes_html = "<h3>Participantes</h3>"
    if participantes:
        participantes_html += "<ul>" + "".join(f"<li>{p}</li>" for p in participantes) + "</ul>"
    else:
        participantes_html += "<p>Não informado.</p>"
    pdf.write_html(participantes_html)

    decisoes_section = "<h3>Assuntos tratados</h3>" + (decisoes_html or "<p>Sem assuntos registrados.</p>")
    pdf.write_html(decisoes_section)

    pdf_output = pdf.output(dest="S")
    try:
        return pdf_output.encode("latin-1")
    except UnicodeEncodeError as exc:
        current_app.logger.warning(
            "Falling back to latin-1 replacement when rendering PDF failed: %s",
            exc,
            exc_info=exc,
        )
        return pdf_output.encode("latin-1", errors="replace")
