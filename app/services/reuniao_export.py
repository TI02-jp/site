"""Geração de PDF de reuniões com cabeçalho timbrado.

Usa o modelo DOTX fornecido em static/models/timbrado - retrato.dotx para
preservar o cabeçalho e converte o conteúdo das decisões (HTML) em DOCX/PDF.
"""

from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path
from shutil import copyfile
from zipfile import ZipFile

from bs4 import BeautifulSoup
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx2pdf import convert as docx2pdf_convert
import pythoncom
from flask import current_app

from app.models.tables import ClienteReuniao


def _slugify(value: str) -> str:
    """Return a filesystem-friendly slug."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "reuniao"


def _materialize_docx_from_template(template_path: Path) -> Path:
    """Convert .dotx template into a .docx so python-docx can open it."""

    tmp_copy = Path(tempfile.mkstemp(suffix=".docx")[1])
    copyfile(template_path, tmp_copy)
    with ZipFile(tmp_copy, "a") as zf:
        try:
            content_xml = zf.read("[Content_Types].xml")
            fixed = content_xml.replace(
                b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
                b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            )
            if fixed != content_xml:
                zf.writestr("[Content_Types].xml", fixed)
        except KeyError:
            pass
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


def export_reuniao_decisoes_pdf(reuniao: ClienteReuniao) -> tuple[bytes, str]:
    """Render decisions into the timbrado template and return PDF bytes + filename."""
    template_path = (
        Path(current_app.root_path) / "static" / "models" / "timbrado - retrato.dotx"
    )
    if not template_path.exists():
        raise FileNotFoundError("Modelo timbrado não encontrado.")

    base_docx_path = _materialize_docx_from_template(template_path)
    doc = Document(str(base_docx_path))

    empresa_nome = getattr(reuniao.empresa, "nome_empresa", "") or "Empresa"
    data_str = reuniao.data.strftime("%d/%m/%Y") if getattr(reuniao, "data", None) else "—"
    setor_nome = getattr(getattr(reuniao, "setor", None), "nome", None) or "Não informado"
    acompanhar_ate_str = reuniao.acompanhar_ate.strftime("%d/%m/%Y") if getattr(reuniao, "acompanhar_ate", None) else "—"

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

    # Monta lista completa de participantes: autor + participantes adicionados
    todos_participantes = []

    # Adiciona o autor da reunião primeiro
    if hasattr(reuniao, 'autor') and reuniao.autor:
        nome_autor = getattr(reuniao.autor, 'nome', None) or getattr(reuniao.autor, 'username', None)
        if nome_autor:
            todos_participantes.append(nome_autor)

    # Adiciona os participantes manuais
    participantes = reuniao.participantes or []
    for participante in participantes:
        if isinstance(participante, dict):
            nome = participante.get("nome", participante.get("name", ""))
            if nome and nome not in todos_participantes:
                todos_participantes.append(nome)
        elif isinstance(participante, str) and participante not in todos_participantes:
            todos_participantes.append(participante)

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

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_docx:
        doc.save(tmp_docx.name)
        tmp_docx_path = Path(tmp_docx.name)

    pdf_bytes: bytes | None = None
    tmp_pdf_path: Path | None = None
    co_initialized = False
    try:
        try:
            pythoncom.CoInitialize()
            co_initialized = True
        except Exception:
            # Se já estiver inicializado, seguimos
            pass

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
            tmp_pdf_path = Path(tmp_pdf.name)
        # docx2pdf gera PDF usando Word/LibreOffice disponíveis
        docx2pdf_convert(str(tmp_docx_path), str(tmp_pdf_path))
        pdf_bytes = tmp_pdf_path.read_bytes()
    finally:
        if co_initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
        try:
            tmp_docx_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            base_docx_path.unlink(missing_ok=True)
        except Exception:
            pass
        if tmp_pdf_path:
            try:
                tmp_pdf_path.unlink(missing_ok=True)
            except Exception:
                pass

    if not pdf_bytes:
        raise RuntimeError("Falha ao gerar PDF da reunião.")

    date_for_name = (
        reuniao.data.strftime("%Y-%m-%d") if getattr(reuniao, "data", None) else datetime.now().date()
    )
    empresa_slug = _slugify(empresa_nome)
    filename = f"reuniao-{empresa_slug}-{date_for_name}.pdf"

    return pdf_bytes, filename
