"""
Serviço de integração com Google Drive API para upload de arquivos.
"""
import os
import io
from typing import Optional, Tuple
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError


class GoogleDriveService:
    """Serviço para interagir com Google Drive API."""

    SCOPES = ['https://www.googleapis.com/auth/drive.file']

    def __init__(self, credentials_path: Optional[str] = None):
        """
        Inicializa o serviço do Google Drive.

        Args:
            credentials_path: Caminho para o arquivo de credenciais JSON.
                            Se não fornecido, usa a variável de ambiente GOOGLE_DRIVE_CREDENTIALS.
        """
        self.credentials_path = credentials_path or os.getenv('GOOGLE_DRIVE_CREDENTIALS')
        self.folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')  # ID da pasta onde os PDFs serão salvos
        self.service = None

        print(f"[GoogleDrive] Inicializando serviço...")
        print(f"[GoogleDrive] Credentials path: {self.credentials_path}")
        print(f"[GoogleDrive] Folder ID: {self.folder_id}")
        print(f"[GoogleDrive] Arquivo existe: {os.path.exists(self.credentials_path) if self.credentials_path else False}")

        if self.credentials_path and os.path.exists(self.credentials_path):
            self._authenticate()
        else:
            print(f"[GoogleDrive] ❌ Não configurado - arquivo de credenciais não encontrado")

    def _authenticate(self):
        """Autentica usando Service Account."""
        try:
            print(f"[GoogleDrive] Autenticando...")
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=self.SCOPES
            )
            self.service = build('drive', 'v3', credentials=credentials)
            print(f"[GoogleDrive] ✓ Autenticação bem-sucedida!")
        except Exception as e:
            print(f"[GoogleDrive] ❌ Erro ao autenticar: {e}")
            self.service = None

    def is_configured(self) -> bool:
        """Verifica se o serviço está configurado corretamente."""
        return self.service is not None

    def upload_pdf(self, file_stream, filename: str, empresa_nome: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Faz upload de um PDF para o Google Drive.

        Args:
            file_stream: Stream do arquivo (FileStorage do Flask)
            filename: Nome original do arquivo
            empresa_nome: Nome da empresa (usado para organização)

        Returns:
            Tuple[bool, str, str]: (sucesso, link_visualizacao, mensagem_erro)
        """
        print(f"[GoogleDrive] upload_pdf chamado - empresa: {empresa_nome}, arquivo: {filename}")
        print(f"[GoogleDrive] Serviço configurado: {self.is_configured()}")

        if not self.is_configured():
            error_msg = "Google Drive não está configurado. Verifique as credenciais."
            print(f"[GoogleDrive] ❌ {error_msg}")
            return False, None, error_msg

        try:
            # Preparar nome do arquivo com prefixo da empresa
            safe_empresa_nome = "".join(c for c in empresa_nome if c.isalnum() or c in (' ', '-', '_')).strip()
            new_filename = f"CFOP_{safe_empresa_nome}_{filename}"
            print(f"[GoogleDrive] Nome do arquivo: {new_filename}")

            # Metadados do arquivo
            file_metadata = {
                'name': new_filename,
                'mimeType': 'application/pdf'
            }

            # Se houver pasta configurada, adicionar como parent
            if self.folder_id:
                file_metadata['parents'] = [self.folder_id]
                print(f"[GoogleDrive] Pasta de destino: {self.folder_id}")
            else:
                print(f"[GoogleDrive] ⚠️ Nenhuma pasta configurada - upload na raiz")

            # Ler conteúdo do arquivo
            file_content = file_stream.read()
            file_stream.seek(0)  # Reset para caso precise ler novamente
            print(f"[GoogleDrive] Tamanho do arquivo: {len(file_content)} bytes")

            # Criar media upload
            media = MediaIoBaseUpload(
                io.BytesIO(file_content),
                mimetype='application/pdf',
                resumable=True
            )

            # Fazer upload
            print(f"[GoogleDrive] Iniciando upload...")
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()

            file_id = file.get('id')
            print(f"[GoogleDrive] Upload concluído - ID: {file_id}")

            # Tornar o arquivo acessível por qualquer pessoa com o link
            print(f"[GoogleDrive] Tornando arquivo público...")
            self.service.permissions().create(
                fileId=file_id,
                body={
                    'type': 'anyone',
                    'role': 'reader'
                }
            ).execute()

            # Obter link de visualização
            view_link = file.get('webViewLink')
            print(f"[GoogleDrive] ✓ Upload bem-sucedido! Link: {view_link}")

            return True, view_link, None

        except HttpError as e:
            error_msg = f"Erro HTTP ao fazer upload: {e.reason}"
            print(f"[GoogleDrive] ❌ {error_msg}")
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Erro ao fazer upload para Google Drive: {str(e)}"
            print(f"[GoogleDrive] ❌ {error_msg}")
            import traceback
            traceback.print_exc()
            return False, None, error_msg

    def delete_file(self, file_url: str) -> Tuple[bool, Optional[str]]:
        """
        Deleta um arquivo do Google Drive usando a URL.

        Args:
            file_url: URL do arquivo no Google Drive (webViewLink)

        Returns:
            Tuple[bool, str]: (sucesso, mensagem_erro)
        """
        if not self.is_configured():
            return False, "Google Drive não está configurado."

        try:
            # Extrair file_id da URL
            # URL format: https://drive.google.com/file/d/FILE_ID/view...
            if '/file/d/' in file_url:
                file_id = file_url.split('/file/d/')[1].split('/')[0]
            elif 'id=' in file_url:
                file_id = file_url.split('id=')[1].split('&')[0]
            else:
                return False, "Formato de URL inválido"

            # Deletar arquivo
            self.service.files().delete(fileId=file_id).execute()
            return True, None

        except HttpError as e:
            error_msg = f"Erro ao deletar arquivo: {e.reason}"
            print(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Erro ao deletar arquivo do Google Drive: {str(e)}"
            print(error_msg)
            return False, error_msg


# Instância global do serviço
drive_service = GoogleDriveService()
