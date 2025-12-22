"""Script de migração para criptografar senhas existentes.

STATUS: ✅ EXECUTADO EM 22/12/2024 - Todas as senhas já estão criptografadas

IMPORTANTE: Execute este script APENAS UMA VEZ após:
1. Gerar e configurar ENCRYPTION_KEY no arquivo .env
2. Atualizar o código com as novas properties de criptografia
3. Fazer backup do banco de dados

Este script irá:
- Buscar todas as consultorias e cadastros de notas com senhas em texto plano
- Criptografar as senhas usando o módulo encryption.py
- Atualizar os registros no banco de dados

USO:
    python migrate_encrypt_passwords.py

ATENÇÃO:
- Certifique-se de ter ENCRYPTION_KEY configurada no .env
- Faça backup do banco ANTES de executar
- Execute apenas uma vez (senhas já criptografadas serão re-criptografadas!)
"""

import os
import sys
import io
from dotenv import load_dotenv

# Configura encoding UTF-8 para stdout no Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Carrega variáveis de ambiente
load_dotenv()

# Verifica se ENCRYPTION_KEY está configurada
if not os.getenv('ENCRYPTION_KEY'):
    print("❌ ERRO: ENCRYPTION_KEY não encontrada no ambiente!")
    print()
    print("Execute o seguinte comando para gerar uma chave:")
    print()
    print("  python -m app.utils.encryption")
    print()
    print("Depois adicione a chave gerada ao arquivo .env")
    sys.exit(1)

# Importa depois de carregar .env
from app import app, db
from app.models.tables import Consultoria, CadastroNota
from app.utils.encryption import encrypt_field

def migrate_consultoria_passwords():
    """Migra senhas de Consultoria para formato criptografado."""
    print("🔄 Migrando senhas de Consultorias...")

    with app.app_context():
        # Busca todas as consultorias
        consultorias = Consultoria.query.all()

        if not consultorias:
            print("   ℹ️  Nenhuma consultoria encontrada")
            return 0

        migrated = 0
        skipped = 0
        errors = 0

        for consultoria in consultorias:
            # Acessa diretamente o campo _senha_encrypted (campo no banco)
            senha_atual = consultoria._senha_encrypted

            if not senha_atual:
                # Sem senha, pular
                skipped += 1
                continue

            # Verifica se já está criptografada (senhas Fernet começam com 'gAAAAA')
            if senha_atual.startswith('gAAAAA'):
                print(f"   ⏭️  '{consultoria.nome}' - Senha já criptografada, pulando...")
                skipped += 1
                continue

            try:
                # Senha está em texto plano, vamos criptografar
                print(f"   🔐 Criptografando senha de '{consultoria.nome}'...")

                # Criptografa a senha em texto plano
                senha_criptografada = encrypt_field(senha_atual)

                # Atualiza diretamente o campo privado (bypass da property)
                consultoria._senha_encrypted = senha_criptografada

                migrated += 1
            except Exception as e:
                print(f"   ❌ Erro ao migrar '{consultoria.nome}': {e}")
                errors += 1

        # Salva todas as mudanças
        if migrated > 0:
            try:
                db.session.commit()
                print(f"   ✅ {migrated} senha(s) de consultoria criptografada(s) com sucesso")
            except Exception as e:
                db.session.rollback()
                print(f"   ❌ Erro ao salvar: {e}")
                return 0

        if skipped > 0:
            print(f"   ⏭️  {skipped} consultoria(s) pulada(s)")
        if errors > 0:
            print(f"   ⚠️  {errors} erro(s) encontrado(s)")

        return migrated

def migrate_cadastronota_passwords():
    """Migra senhas de CadastroNota para formato criptografado."""
    print()
    print("🔄 Migrando senhas de Cadastros de Notas...")

    with app.app_context():
        # Busca todos os cadastros de notas
        cadastros = CadastroNota.query.all()

        if not cadastros:
            print("   ℹ️  Nenhum cadastro de nota encontrado")
            return 0

        migrated = 0
        skipped = 0
        errors = 0

        for cadastro in cadastros:
            # Acessa diretamente o campo _senha_encrypted (campo no banco)
            senha_atual = cadastro._senha_encrypted

            if not senha_atual:
                # Sem senha, pular
                skipped += 1
                continue

            # Verifica se já está criptografada (senhas Fernet começam com 'gAAAAA')
            if senha_atual.startswith('gAAAAA'):
                print(f"   ⏭️  Cadastro '{cadastro.cadastro}' - Senha já criptografada, pulando...")
                skipped += 1
                continue

            try:
                # Senha está em texto plano, vamos criptografar
                print(f"   🔐 Criptografando senha de cadastro '{cadastro.cadastro}'...")

                # Criptografa a senha em texto plano
                senha_criptografada = encrypt_field(senha_atual)

                # Atualiza diretamente o campo privado (bypass da property)
                cadastro._senha_encrypted = senha_criptografada

                migrated += 1
            except Exception as e:
                print(f"   ❌ Erro ao migrar cadastro '{cadastro.cadastro}': {e}")
                errors += 1

        # Salva todas as mudanças
        if migrated > 0:
            try:
                db.session.commit()
                print(f"   ✅ {migrated} senha(s) de cadastro criptografada(s) com sucesso")
            except Exception as e:
                db.session.rollback()
                print(f"   ❌ Erro ao salvar: {e}")
                return 0

        if skipped > 0:
            print(f"   ⏭️  {skipped} cadastro(s) pulado(s)")
        if errors > 0:
            print(f"   ⚠️  {errors} erro(s) encontrado(s)")

        return migrated

def main():
    """Executa migração completa."""
    print("=" * 70)
    print("MIGRAÇÃO DE SENHAS PARA FORMATO CRIPTOGRAFADO")
    print("=" * 70)
    print()
    print("⚠️  ATENÇÃO: Certifique-se de ter feito BACKUP do banco de dados!")
    print()

    resposta = input("Deseja continuar? (s/N): ").strip().lower()
    if resposta != 's':
        print()
        print("❌ Migração cancelada pelo usuário")
        sys.exit(0)

    print()
    print("🚀 Iniciando migração...")
    print()

    # Migra consultorias
    total_consultorias = migrate_consultoria_passwords()

    # Migra cadastros de notas
    total_cadastros = migrate_cadastronota_passwords()

    # Resumo
    print()
    print("=" * 70)
    print("RESUMO DA MIGRAÇÃO")
    print("=" * 70)
    print(f"  Consultorias migradas: {total_consultorias}")
    print(f"  Cadastros migrados: {total_cadastros}")
    print(f"  Total: {total_consultorias + total_cadastros} senha(s) criptografada(s)")
    print("=" * 70)
    print()

    if total_consultorias > 0 or total_cadastros > 0:
        print("✅ Migração concluída com sucesso!")
        print()
        print("PRÓXIMOS PASSOS:")
        print("1. Verifique se as senhas ainda estão visíveis na interface")
        print("2. Verifique se as senhas estão criptografadas no banco de dados")
        print("3. Teste o login/acesso com as credenciais migradas")
    else:
        print("ℹ️  Nenhuma senha foi migrada (todas já estavam criptografadas ou vazias)")

    print()

if __name__ == "__main__":
    main()
