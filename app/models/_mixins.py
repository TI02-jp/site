"""
Mixins reutilizaveis para models SQLAlchemy.

Este modulo contem classes mixin que fornecem funcionalidades
comuns para multiplos models, evitando duplicacao de codigo.

Mixins Disponiveis:
    - AttachmentMixin: Propriedades comuns para models de anexos

Uso:
    class TaskAttachment(AttachmentMixin, db.Model):
        __tablename__ = "task_attachments"
        ...

Autor: Refatoracao automatizada
Data: 2024
"""

import os


# Extensoes de imagem reconhecidas
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# Extensoes de texto reconhecidas
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log"}


class AttachmentMixin:
    """
    Mixin com propriedades comuns para models de anexo.

    Fornece propriedades computadas para identificar tipo de arquivo
    e obter nome de exibicao amigavel.

    Requer que a classe que herda tenha os atributos:
        - file_path: str - Caminho do arquivo
        - original_name: str | None - Nome original do arquivo
        - mime_type: str | None - MIME type do arquivo

    Propriedades:
        - extension: Extensao do arquivo em minusculas
        - is_image: True se anexo e imagem
        - is_pdf: True se anexo e PDF
        - is_text: True se anexo e texto
        - display_name: Nome amigavel para exibicao
    """

    @property
    def extension(self) -> str:
        """
        Retorna extensao do arquivo em minusculas.

        Returns:
            str: Extensao com ponto (ex: ".pdf", ".jpg") ou string vazia.
        """
        _, ext = os.path.splitext(getattr(self, 'file_path', '') or "")
        return ext.lower()

    @property
    def is_image(self) -> bool:
        """
        Verifica se anexo e uma imagem.

        Verifica primeiro pelo MIME type e depois pela extensao.

        Returns:
            bool: True se anexo e imagem.
        """
        mime_type = getattr(self, 'mime_type', None)
        if mime_type and mime_type.startswith("image/"):
            return True
        return self.extension in IMAGE_EXTENSIONS

    @property
    def is_pdf(self) -> bool:
        """
        Verifica se anexo e um documento PDF.

        Verifica primeiro pelo MIME type e depois pela extensao.

        Returns:
            bool: True se anexo e PDF.
        """
        mime_type = getattr(self, 'mime_type', None)
        if mime_type:
            return mime_type == "application/pdf"
        return self.extension == ".pdf"

    @property
    def is_text(self) -> bool:
        """
        Verifica se anexo e um arquivo de texto.

        Verifica primeiro pelo MIME type e depois pela extensao.

        Returns:
            bool: True se anexo e texto.
        """
        mime_type = getattr(self, 'mime_type', None)
        if mime_type and mime_type.startswith("text/"):
            return True
        return self.extension in TEXT_EXTENSIONS

    @property
    def display_name(self) -> str:
        """
        Retorna nome amigavel do anexo para exibicao.

        Usa nome original se disponivel, caso contrario
        extrai nome do caminho do arquivo.

        Returns:
            str: Nome para exibicao.
        """
        original = getattr(self, 'original_name', None)
        if original:
            return original
        file_path = getattr(self, 'file_path', '') or ""
        return os.path.basename(file_path)

    @property
    def is_office_document(self) -> bool:
        """
        Verifica se anexo e um documento Office.

        Returns:
            bool: True se anexo e Word, Excel ou PowerPoint.
        """
        office_extensions = {
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".odt", ".ods", ".odp"
        }
        return self.extension in office_extensions

    @property
    def file_type_icon(self) -> str:
        """
        Retorna classe de icone Bootstrap Icons para o tipo de arquivo.

        Returns:
            str: Classe CSS do icone (ex: "bi-file-image").
        """
        if self.is_image:
            return "bi-file-image"
        if self.is_pdf:
            return "bi-file-pdf"
        if self.is_text:
            return "bi-file-text"
        if self.is_office_document:
            if self.extension in {".doc", ".docx", ".odt"}:
                return "bi-file-word"
            if self.extension in {".xls", ".xlsx", ".ods"}:
                return "bi-file-excel"
            if self.extension in {".ppt", ".pptx", ".odp"}:
                return "bi-file-ppt"
        return "bi-file-earmark"
