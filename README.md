# Portal JP Contabil

Aplicacao web interna para operacao e gestao de processos da JP Contabil.

## O que e este projeto

O portal centraliza fluxos internos como tarefas, usuarios, empresas, reunioes, notificacoes, comunicados, cursos, procedimentos operacionais e modulos administrativos.

## Stack principal

- Backend: Python + Flask
- Servidor de aplicacao: Waitress
- Banco de dados: MySQL (com fallback SQLite em desenvolvimento)
- ORM e migracoes: SQLAlchemy + Alembic/Flask-Migrate
- Frontend: Jinja2 + JavaScript + CSS
- Infra de borda: Apache como proxy reverso

## Como executar localmente

1. Criar e ativar ambiente virtual.
2. Instalar dependencias:

```bash
pip install -r requirements.txt
```

3. Criar arquivo `.env` (base em `.env.example`) com as variaveis de banco e seguranca.
4. Executar migracoes:

```bash
flask db upgrade
```

5. Iniciar aplicacao:

```bash
python run.py
```

## Estrutura resumida

- `app/` codigo da aplicacao (rotas, servicos, modelos, templates, estaticos)
- `migrations/` migracoes de banco
- `docs/` documentacao tecnica
- `run.py` entrada da aplicacao

## Documentacao tecnica

- `docs/README.md` indice dos documentos
- `docs/ARQUITETURA_APLICACAO.md` arquitetura interna da aplicacao
- `docs/API_DOCUMENTATION.md` API e contratos
- `docs/FLUXOGRAMAS.md` fluxos funcionais e operacionais
- `docs/PROXY_REVERSO.md` arquitetura Apache + Flask
- `docs/MANUTENCAO_APACHE.md` operacao/manutencao Apache
- `docs/QUICK_REFERENCE.md` referencia rapida

## Observacoes

- Este projeto e de uso interno.
- Nao versionar segredos e credenciais.
