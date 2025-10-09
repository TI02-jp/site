# 📋 Portal de Procedimentos - JP Contábil

## 📖 Sumário
- [Visão Geral](#visão-geral)
- [Arquitetura do Sistema](#arquitetura-do-sistema)
- [Tecnologias Utilizadas](#tecnologias-utilizadas)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Instalação e Configuração](#instalação-e-configuração)
- [Modelos de Dados](#modelos-de-dados)
- [Rotas e Funcionalidades](#rotas-e-funcionalidades)
- [Segurança](#segurança)
- [Manutenção e Atualizações](#manutenção-e-atualizações)

---

## 🎯 Visão Geral

O **Portal de Procedimentos** é uma aplicação web interna desenvolvida para substituir o gerenciamento de procedimentos que anteriormente era feito via Google Drive. O sistema permite o cadastro de usuários, autenticação segura e futuramente o gerenciamento de procedimentos empresariais.

### Objetivos
- Centralizar procedimentos internos
- Controlar acesso através de autenticação
- Facilitar a gestão e atualização de documentos
- Manter histórico de alterações

---

## 🏗️ Arquitetura do Sistema

O sistema segue o padrão **MVC (Model-View-Controller)** adaptado para Flask:

```
┌─────────────────────────────────────────────────────────┐
│                      CLIENTE (Browser)                   │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   FLASK APPLICATION                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Templates   │  │ Controllers  │  │    Models    │  │
│  │   (Views)    │◄─┤  (Routes)    │◄─┤  (Database)  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│         │                  │                  │          │
│         │                  │                  │          │
│  ┌──────▼──────────────────▼──────────────────▼──────┐  │
│  │          Flask Extensions & Middleware            │  │
│  │  • SQLAlchemy  • Flask-Migrate  • Flask-WTF      │  │
│  │  • CSRFProtect • Werkzeug Security               │  │
│  └───────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   MySQL DATABASE                         │
│  ┌────────────┐           ┌────────────┐               │
│  │   users    │───────────│   posts    │               │
│  └────────────┘           └────────────┘               │
└─────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tecnologias Utilizadas

### Backend
- **Python 3.x** - Linguagem de programação
- **Flask 2.x** - Framework web
- **Flask-SQLAlchemy** - ORM para banco de dados
- **Flask-Migrate** - Gerenciamento de migrações
- **Flask-WTF** - Formulários e validação
- **Werkzeug** - Criptografia de senhas

### Frontend
- **HTML5** - Estrutura das páginas
- **CSS3** - Estilização
- **Jinja2** - Template engine

### Banco de Dados
- **MySQL 8.x** - Sistema de gerenciamento de banco de dados
- **mysql-connector-python** - Driver de conexão

### Segurança
- **CSRF Protection** - Proteção contra ataques CSRF
- **Password Hashing** - Senhas criptografadas com PBKDF2
- **Environment Variables** - Credenciais em arquivo .env

---

## 📁 Estrutura do Projeto

```
projeto/
│
├── app/
│   ├── __init__.py              # Inicialização da aplicação
│   ├── loginForms.py            # Formulários de autenticação
│   │
│   ├── controllers/
│   │   └── default.py           # Rotas e lógica de controle
│   │
│   ├── models/
│   │   └── tables.py            # Modelos do banco de dados
│   │
│   ├── static/
│   │   └── styles.css           # Estilos CSS
│   │
│   └── templates/
│       ├── home.html            # Página inicial
│       ├── login.html           # Página de login
│       ├── register.html        # Página de registro
│       └── list_users.html      # Listagem de usuários
│
├── migrations/                   # Migrações do banco de dados
│   ├── env.py
│   ├── alembic.ini
│   └── versions/
│
├── .env                         # Variáveis de ambiente (não versionado)
├── .gitignore                   # Arquivos ignorados pelo Git
├── database.py                  # Script de gerenciamento do BD
├── run.py                       # Ponto de entrada da aplicação
└── requirements.txt             # Dependências do projeto
```

---

## ⚙️ Instalação e Configuração

### Pré-requisitos
- Python 3.8 ou superior
- MySQL 8.0 ou superior
- pip (gerenciador de pacotes Python)
- Git

### Passo 1: Clonar o Repositório
```bash
git clone <url-do-repositorio>
cd projeto
```

### Passo 2: Criar Ambiente Virtual
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### Passo 3: Instalar Dependências
```bash
pip install -r requirements.txt
```

### Passo 4: Configurar Variáveis de Ambiente
Crie um arquivo `.env` na raiz do projeto:

```env
# Configurações do Banco de Dados
DB_HOST=localhost
DB_NAME=cadastro_empresas
DB_USER=root
DB_PASSWORD=sua_senha_aqui

# Chave Secreta do Flask
SECRET_KEY=sua_chave_secreta_aleatoria_aqui
```

**⚠️ IMPORTANTE**: Nunca commite o arquivo `.env` no Git!

### Passo 5: Criar o Banco de Dados
```sql
CREATE DATABASE cadastro_empresas CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### Passo 6: Executar Migrações
```bash
# Inicializar migrações (apenas na primeira vez)
flask db init

# Criar migração
flask db migrate -m "Initial migration"

# Aplicar migração
flask db upgrade
```

### Passo 7: Executar a Aplicação
```bash
python run.py
```

A aplicação estará disponível em: `http://localhost:5000`

---

## 🗄️ Modelos de Dados

### Tabela: `users`

| Campo    | Tipo         | Restrições           | Descrição              |
|----------|--------------|----------------------|------------------------|
| id       | INTEGER      | PRIMARY KEY, AUTO    | Identificador único    |
| username | VARCHAR(80)  | UNIQUE, NOT NULL     | Nome de usuário        |
| password | VARCHAR(120) | NOT NULL             | Senha criptografada    |
| name     | VARCHAR(100) | NOT NULL             | Nome completo          |
| email    | VARCHAR(120) | UNIQUE, NOT NULL     | Email do usuário       |

### Tabela: `posts`

| Campo    | Tipo       | Restrições           | Descrição              |
|----------|------------|----------------------|------------------------|
| id       | INTEGER    | PRIMARY KEY, AUTO    | Identificador único    |
| content  | TEXT       | NOT NULL             | Conteúdo do post       |
| id_user  | INTEGER    | FOREIGN KEY, NOT NULL| Referência ao usuário  |

### Relacionamentos
- Um `User` pode ter muitos `Posts` (1:N)
- Um `Post` pertence a um `User`

---

## 🛣️ Rotas e Funcionalidades

### Rotas Públicas

#### `GET /`
- **Descrição**: Página inicial do sistema
- **Template**: `home.html`
- **Funcionalidade**: Apresenta o sistema e links para login/registro

#### `GET /login`
- **Descrição**: Exibe formulário de login
- **Template**: `login.html`
- **Funcionalidade**: Permite acesso ao sistema

#### `POST /login`
- **Descrição**: Processa login do usuário
- **Validações**:
  - Username obrigatório
  - Password obrigatório
- **Fluxo**:
  1. Valida formulário
  2. Busca usuário no banco
  3. Verifica senha criptografada
  4. Redireciona para home ou exibe erro

#### `GET /register`
- **Descrição**: Exibe formulário de cadastro
- **Template**: `register.html`
- **Funcionalidade**: Permite criar nova conta

#### `POST /register`
- **Descrição**: Processa cadastro de novo usuário
- **Validações**:
  - Username: 4-20 caracteres
  - Email: formato válido
  - Senha: mínimo 6 caracteres
  - Confirmação de senha
- **Fluxo**:
  1. Valida formulário
  2. Verifica duplicidade (username/email)
  3. Criptografa senha
  4. Salva no banco
  5. Redireciona para login

### Rotas de Diagnóstico

#### `GET /test_connection`
- **Descrição**: Testa conexão com banco de dados
- **Resposta**: Mensagem de sucesso/erro

#### `GET /users`
- **Descrição**: Lista todos os usuários
- **Template**: `list_users.html`
- **Funcionalidade**: Exibe usuários cadastrados

---

## 🔒 Segurança

### Proteção CSRF
- Todos os formulários incluem token CSRF
- Validação automática via Flask-WTF
- Proteção contra ataques Cross-Site Request Forgery

### Criptografia de Senhas
- Algoritmo: **PBKDF2-SHA256**
- Biblioteca: Werkzeug Security
- Senhas nunca armazenadas em texto plano
- Salt automático para cada senha

### Variáveis de Ambiente
- Credenciais sensíveis em arquivo `.env`
- Arquivo `.env` no `.gitignore`
- Separação entre configuração e código

### Validações
- **Frontend**: HTML5 validation
- **Backend**: WTForms validators
- Sanitização automática de inputs

---

## 🔄 Fluxos de Processo

### Fluxo de Registro

```
┌─────────────┐
│   Início    │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│ Usuário acessa      │
│ /register           │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ Preenche formulário │
│ - Username          │
│ - Email             │
│ - Nome              │
│ - Senha             │
│ - Confirmar Senha   │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ Valida formulário   │
└──────┬──────────────┘
       │
       ├─── Inválido ───┐
       │                │
       ▼                ▼
┌─────────────┐   ┌──────────────┐
│ Verifica    │   │ Exibe erros  │
│ duplicidade │   │ no formulário│
└──────┬──────┘   └──────────────┘
       │
       ├─── Existe ─────┐
       │                │
       ▼                ▼
┌─────────────┐   ┌──────────────┐
│ Criptografa │   │ Mensagem:    │
│ senha       │   │ "Usuário já  │
└──────┬──────┘   │  cadastrado" │
       │          └──────────────┘
       ▼
┌─────────────┐
│ Salva no BD │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Redireciona │
│ para /login │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│     Fim     │
└─────────────┘
```

### Fluxo de Login

```
┌─────────────┐
│   Início    │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│ Usuário acessa      │
│ /login              │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ Preenche formulário │
│ - Username          │
│ - Password          │
│ - Remember Me       │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ Valida formulário   │
└──────┬──────────────┘
       │
       ├─── Inválido ───┐
       │                │
       ▼                ▼
┌─────────────┐   ┌──────────────┐
│ Busca user  │   │ Exibe erros  │
│ no BD       │   └──────────────┘
└──────┬──────┘
       │
       ├─── Não encontrado ─┐
       │                    │
       ▼                    ▼
┌─────────────┐   ┌──────────────┐
│ Verifica    │   │ Mensagem:    │
│ senha hash  │   │ "Credenciais │
└──────┬──────┘   │  inválidas"  │
       │          └──────────────┘
       ├─── Incorreta ──────┘
       │
       ▼
┌─────────────┐
│ Login OK    │
│ (Session)   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Redireciona │
│ para /home  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│     Fim     │
└─────────────┘
```

---

## 🔧 Manutenção e Atualizações

### Adicionando Novos Campos ao Modelo

1. **Editar o modelo** em `app/models/tables.py`
```python
class User(db.Model):
    # ... campos existentes ...
    novo_campo = db.Column(db.String(50))
```

2. **Criar migração**
```bash
flask db migrate -m "Adiciona novo_campo em User"
```

3. **Aplicar migração**
```bash
flask db upgrade
```

### Criando Novas Rotas

1. **Adicionar rota** em `app/controllers/default.py`
```python
@app.route('/nova-rota')
def nova_funcionalidade():
    return render_template('template.html')
```

2. **Criar template** em `app/templates/`

3. **Adicionar formulário** (se necessário) em `app/loginForms.py`

### Backup do Banco de Dados

```bash
# Backup
mysqldump -u root -p cadastro_empresas > backup_$(date +%Y%m%d).sql

# Restaurar
mysql -u root -p cadastro_empresas < backup_20250101.sql
```

### Logs e Debugging

- **Modo Debug**: Ativado em `run.py` (`debug=True`)
- **Logs**: Configurados em `database.py`
- **Erros**: Flask exibe traceback detalhado em modo debug

⚠️ **NUNCA use `debug=True` em produção!**

---

## 📝 Próximas Funcionalidades

### Planejadas
- [ ] Sistema de autenticação com sessões (Flask-Login)
- [ ] Recuperação de senha via email
- [ ] Gerenciamento de procedimentos
- [ ] Upload de arquivos
- [ ] Sistema de permissões por níveis
- [ ] Histórico de alterações
- [ ] API REST para integração
- [ ] Dashboard administrativo

### Melhorias de Segurança
- [ ] Rate limiting em rotas de login
- [ ] Logs de auditoria
- [ ] 2FA (Two-Factor Authentication)
- [ ] Política de senhas fortes

---

## 👥 Equipe e Contribuição

### Como Contribuir

1. **Fork** o projeto
2. Crie uma **branch** para sua feature (`git checkout -b feature/NovaFuncionalidade`)
3. **Commit** suas mudanças (`git commit -m 'Adiciona nova funcionalidade'`)
4. **Push** para a branch (`git push origin feature/NovaFuncionalidade`)
5. Abra um **Pull Request**

### Padrões de Código

- **PEP 8** para Python
- Comentários em português
- Nomes de variáveis descritivos
- Documentação em docstrings

---

## 📞 Suporte

Para dúvidas ou problemas:
- Abra uma **issue** no repositório
- Entre em contato com a equipe de TI
- Consulte a documentação do Flask: https://flask.palletsprojects.com

---

## 📄 Licença

© 2025 JP Contábil. Todos os direitos reservados.  
Este projeto é de uso interno exclusivo.

---

**Versão da Documentação**: 1.0  
**Última Atualização**: Outubro 2025  
**Autor**: Equipe de Desenvolvimento JP Contábil
