# Fluxogramas do Sistema - Portal JP Contábil

Este documento apresenta fluxogramas visuais detalhados do funcionamento completo do sistema.

---

## Índice

1. [Fluxograma de Requisição HTTP/HTTPS](#1-fluxograma-de-requisição-httphttps)
2. [Fluxograma de Autenticação](#2-fluxograma-de-autenticação)
3. [Fluxograma de Inicialização do Sistema](#3-fluxograma-de-inicialização-do-sistema)
4. [Fluxograma de Tratamento de Erros](#4-fluxograma-de-tratamento-de-erros)
5. [Fluxograma de API REST](#5-fluxograma-de-api-rest)
6. [Fluxograma de Renovação SSL](#6-fluxograma-de-renovação-ssl)
7. [Fluxograma de Backup](#7-fluxograma-de-backup)
8. [Arquitetura de Camadas](#8-arquitetura-de-camadas)

---

## 1. Fluxograma de Requisição HTTP/HTTPS

### Requisição Completa: Cliente → Servidor → Banco de Dados

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          INÍCIO DA REQUISIÇÃO                           │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Usuário acessa URL no      │
                    │ navegador                  │
                    │ https://portal.jpcontabil  │
                    │ .com.br/tasks              │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ DNS Lookup                 │
                    │ Resolve domínio → IP       │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Navegador conecta em       │
                    │ IP:443 (HTTPS)             │
                    └────────────┬───────────────┘
                                 │
                                 ▼
        ╔════════════════════════════════════════════════════════╗
        ║           APACHE HTTP SERVER (Porta 443)               ║
        ║                  Proxy Reverso                         ║
        ╚════════════════════════════════════════════════════════╝
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Handshake SSL/TLS          │
                    │ • ClientHello              │
                    │ • ServerHello              │
                    │ • Certificado apresentado  │
                    │ • Validação do certificado │
                    │ • Chave de sessão gerada   │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Conexão HTTPS estabelecida │
                    │ Dados criptografados       │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Apache recebe requisição:  │
                    │ GET /tasks HTTP/1.1        │
                    │ Host: portal.jpcontabil... │
                    │ Cookie: session=abc123     │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ VirtualHost Matching       │
                    │ ServerName: portal.jp...   │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Apache descriptografa SSL  │
                    │ HTTPS → HTTP               │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Apache adiciona headers:   │
                    │ X-Forwarded-Proto: https   │
                    │ X-Forwarded-For: [IP]      │
                    │ X-Forwarded-Host: portal...│
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ ProxyPass executa:         │
                    │ http://127.0.0.1:9000/tasks│
                    └────────────┬───────────────┘
                                 │
                                 ▼
        ╔════════════════════════════════════════════════════════╗
        ║         WAITRESS WSGI SERVER (Porta 9000)              ║
        ║                  Localhost only                        ║
        ╚════════════════════════════════════════════════════════╝
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Waitress valida origem:    │
                    │ IP = 127.0.0.1? ✓          │
                    │ Trusted proxy? ✓           │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Waitress processa headers  │
                    │ X-Forwarded-Proto → HTTPS  │
                    │ request.is_secure = True   │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Waitress aloca thread      │
                    │ do pool (1 de 32)          │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Chama Flask via WSGI       │
                    │ environ = {...}            │
                    └────────────┬───────────────┘
                                 │
                                 ▼
        ╔════════════════════════════════════════════════════════╗
        ║              FLASK APPLICATION                         ║
        ║             Lógica de Negócio                          ║
        ╚════════════════════════════════════════════════════════╝
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Flask Router               │
                    │ URL: /tasks                │
                    │ → Blueprint: tasks_bp      │
                    │ → Function: tasks_overview │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Flask-Login                │
                    │ Verifica cookie session    │
                    │ Carrega current_user       │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
             ┌──────│ Usuário autenticado?       │
             │      └────────────┬───────────────┘
             │                   │
             │ NÃO               │ SIM
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ CSRF Token válido?         │
             │      │ (se POST/PUT/DELETE)       │
             │      └────────────┬───────────────┘
             │                   │
             │                   │ SIM
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ Executa lógica de negócio: │
             │      │ def tasks_overview():      │
             │      │   tasks = Task.query...    │
             │      └────────────┬───────────────┘
             │                   │
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ SQLAlchemy ORM             │
             │      │ Monta query SQL            │
             │      └────────────┬───────────────┘
             │                   │
             │                   ▼
             │      ╔════════════════════════════════════════════╗
             │      ║   MYSQL DATABASE (Porta 3306, localhost)   ║
             │      ╚════════════════════════════════════════════╝
             │                   │
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ Executa query:             │
             │      │ SELECT * FROM tasks        │
             │      │ WHERE user_id = ?          │
             │      └────────────┬───────────────┘
             │                   │
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ MySQL retorna resultados   │
             │      │ [Task 1, Task 2, ...]      │
             │      └────────────┬───────────────┘
             │                   │
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ SQLAlchemy mapeia para     │
             │      │ objetos Python (Task)      │
             │      └────────────┬───────────────┘
             │                   │
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ Flask renderiza template:  │
             │      │ render_template(           │
             │      │   'tasks.html',            │
             │      │   tasks=tasks              │
             │      │ )                          │
             │      └────────────┬───────────────┘
             │                   │
             │                   ▼
             │      ┌────────────────────────────┐
             │      │ Jinja2 gera HTML           │
             │      │ com dados das tarefas      │
             │      └────────────┬───────────────┘
             │                   │
             ▼                   ▼
   ┌────────────────┐ ┌────────────────────────────┐
   │ Retorna 302    │ │ Retorna 200 OK             │
   │ Redirect para  │ │ Content-Type: text/html    │
   │ /login         │ │ Set-Cookie: session=...    │
   └────────┬───────┘ │ Body: [HTML gerado]        │
            │         └────────────┬───────────────┘
            │                      │
            └──────────┬───────────┘
                       │
                       ▼
          ┌────────────────────────────┐
          │ Flask → Waitress           │
          │ Retorna HTTP response      │
          └────────────┬───────────────┘
                       │
                       ▼
          ┌────────────────────────────┐
          │ Waitress → Apache          │
          │ HTTP response via socket   │
          └────────────┬───────────────┘
                       │
                       ▼
          ┌────────────────────────────┐
          │ Apache recebe resposta     │
          │ HTTP (sem criptografia)    │
          └────────────┬───────────────┘
                       │
                       ▼
          ┌────────────────────────────┐
          │ Apache aplica otimizações: │
          │ • Compressão GZIP          │
          │ • Headers de segurança     │
          │ • Headers de cache         │
          └────────────┬───────────────┘
                       │
                       ▼
          ┌────────────────────────────┐
          │ Apache criptografa com SSL │
          │ HTTP → HTTPS               │
          └────────────┬───────────────┘
                       │
                       ▼
          ┌────────────────────────────┐
          │ Apache envia resposta para │
          │ cliente (HTTPS, porta 443) │
          └────────────┬───────────────┘
                       │
                       ▼
          ┌────────────────────────────┐
          │ Cliente recebe resposta    │
          │ • Descriptografa SSL       │
          │ • Descomprime GZIP         │
          │ • Parseia HTML             │
          │ • Renderiza página         │
          └────────────┬───────────────┘
                       │
                       ▼
        ┌──────────────────────────────────┐
        │     USUÁRIO VÊ PÁGINA DE         │
        │          TAREFAS                 │
        └──────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────┐
        │          FIM DA REQUISIÇÃO       │
        └──────────────────────────────────┘

Tempo total: ~100-300ms (dependendo da complexidade da query)
```

---

## 2. Fluxograma de Autenticação

### Login com Email/Senha

```
                        ┌─────────────────┐
                        │   INÍCIO        │
                        │ Usuário acessa  │
                        │   /login        │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Flask renderiza formulário │
                    │ de login (GET /login)      │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Usuário preenche:          │
                    │ • Email                    │
                    │ • Senha                    │
                    │ • CSRF token (hidden)      │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Submit (POST /login)       │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Flask valida CSRF token    │
                    └────────────┬───────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                VÁLIDO?                   INVÁLIDO
                    │                         │
                    │ SIM                     │ NÃO
                    ▼                         ▼
       ┌────────────────────────┐  ┌────────────────────────┐
       │ Busca usuário no banco │  │ Retorna erro 403       │
       │ User.query.filter_by(  │  │ "CSRF token inválido"  │
       │   email=email          │  └────────────────────────┘
       │ ).first()              │
       └────────────┬───────────┘
                    │
                    ▼
       ┌────────────────────────┐
       │ Usuário encontrado?    │
       └────────────┬───────────┘
                    │
       ┌────────────┴────────────┐
       │                         │
     SIM                        NÃO
       │                         │
       ▼                         ▼
┌──────────────────┐  ┌────────────────────────┐
│ Verifica senha:  │  │ Flash message:         │
│ check_password(  │  │ "Email ou senha        │
│   password       │  │  inválidos"            │
│ )                │  │ Redirect → /login      │
└──────┬───────────┘  └────────────────────────┘
       │
       ▼
┌──────────────────┐
│ Senha correta?   │
└──────┬───────────┘
       │
  ┌────┴────┐
  │         │
 SIM       NÃO
  │         │
  ▼         ▼
┌─────┐  ┌────────────────────────┐
│ ✓   │  │ Flash message:         │
└──┬──┘  │ "Email ou senha        │
   │     │  inválidos"            │
   │     │ Redirect → /login      │
   │     └────────────────────────┘
   │
   ▼
┌────────────────────────┐
│ Verifica se usuário    │
│ está ativo             │
│ if user.is_active:     │
└────────────┬───────────┘
             │
        ┌────┴────┐
        │         │
      SIM        NÃO
        │         │
        ▼         ▼
     ┌─────┐  ┌────────────────────────┐
     │ ✓   │  │ Flash message:         │
     └──┬──┘  │ "Conta desativada"     │
        │     │ Redirect → /login      │
        │     └────────────────────────┘
        │
        ▼
┌────────────────────────┐
│ Flask-Login:           │
│ login_user(user)       │
│ Cria sessão no servidor│
└────────────┬───────────┘
             │
             ▼
┌────────────────────────┐
│ Gera session_id        │
│ Armazena no servidor:  │
│ sessions[sid] = {      │
│   user_id: 42,         │
│   logged_in: True      │
│ }                      │
└────────────┬───────────┘
             │
             ▼
┌────────────────────────┐
│ Retorna cookie:        │
│ Set-Cookie: session=   │
│   [session_id];        │
│   HttpOnly; Secure;    │
│   SameSite=Lax         │
└────────────┬───────────┘
             │
             ▼
┌────────────────────────┐
│ Redirect para:         │
│ next_page ou /         │
│ (página inicial)       │
└────────────┬───────────┘
             │
             ▼
┌────────────────────────┐
│ Usuário está logado    │
│ current_user disponível│
│ em todas as rotas      │
└────────────┬───────────┘
             │
             ▼
      ┌─────────────┐
      │     FIM     │
      └─────────────┘
```

### Login com Google OAuth 2.0

```
                        ┌─────────────────┐
                        │   INÍCIO        │
                        │ Usuário clica   │
                        │ "Login Google"  │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Redirect para:             │
                    │ https://accounts.google    │
                    │ .com/o/oauth2/auth         │
                    │ ?client_id=...             │
                    │ &redirect_uri=.../callback │
                    │ &scope=email profile       │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Usuário faz login no       │
                    │ Google (se necessário)     │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Usuário autoriza acesso:   │
                    │ • Email                    │
                    │ • Profile (nome, foto)     │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Google redireciona para:   │
                    │ /google/callback?code=ABC  │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Flask recebe código        │
                    │ Troca código por token:    │
                    │ POST https://oauth2.google │
                    │ apis.com/token             │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Google retorna:            │
                    │ • access_token             │
                    │ • id_token (JWT)           │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Flask decodifica id_token: │
                    │ • email                    │
                    │ • name                     │
                    │ • picture                  │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Busca usuário no banco:    │
                    │ User.query.filter_by(      │
                    │   email=google_email       │
                    │ ).first()                  │
                    └────────────┬───────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
              ENCONTRADO                   NÃO ENCONTRADO
                    │                         │
                    ▼                         ▼
       ┌────────────────────┐    ┌────────────────────────┐
       │ Atualiza dados:    │    │ Cria novo usuário:     │
       │ • nome             │    │ User(                  │
       │ • foto             │    │   email=google_email,  │
       │ • last_login       │    │   name=google_name,    │
       └────────┬───────────┘    │   picture=google_pic   │
                │                │ )                      │
                │                │ db.session.add(user)   │
                │                │ db.session.commit()    │
                │                └────────────┬───────────┘
                │                             │
                └────────────┬────────────────┘
                             │
                             ▼
                ┌────────────────────────────┐
                │ login_user(user)           │
                │ Cria sessão                │
                └────────────┬───────────────┘
                             │
                             ▼
                ┌────────────────────────────┐
                │ Redirect para /            │
                │ (página inicial)           │
                └────────────┬───────────────┘
                             │
                             ▼
                      ┌─────────────┐
                      │     FIM     │
                      └─────────────┘
```

---

## 3. Fluxograma de Inicialização do Sistema

### Startup Completo do Sistema

```
                        ┌─────────────────┐
                        │ BOOT DO WINDOWS │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Windows Service Manager    │
                    │ inicia serviços marcados   │
                    │ como "Automático"          │
                    └────────────┬───────────────┘
                                 │
                 ┌───────────────┼───────────────┐
                 │               │               │
                 ▼               ▼               ▼
        ┌────────────┐  ┌────────────┐  ┌────────────┐
        │   Tcpip    │  │    Afd     │  │  Outros    │
        │  (Rede)    │  │  (Sockets) │  │  serviços  │
        └──────┬─────┘  └──────┬─────┘  └────────────┘
               │                │
               └────────┬───────┘
                        │ (Dependências satisfeitas)
                        ▼
            ┌───────────────────────────┐
            │   Apache2.4 Service       │
            │   Tipo: Automático        │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Executa:                  │
            │ C:\xampp\apache\bin\      │
            │ httpd.exe -k runservice   │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ httpd.exe lê:             │
            │ C:\xampp\apache\conf\     │
            │ httpd.conf                │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Carrega módulos:          │
            │ • mod_proxy.so            │
            │ • mod_proxy_http.so       │
            │ • mod_ssl.so              │
            │ • mod_deflate.so          │
            │ • mod_headers.so          │
            │ • ... (40+ módulos)       │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Lê arquivos Include:      │
            │ • httpd-vhosts.conf       │
            │ • httpd-ssl.conf          │
            │ • httpd-xampp.conf        │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Bind em portas:           │
            │ • 0.0.0.0:80 (HTTP)       │
            │ • 0.0.0.0:443 (HTTPS)     │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Carrega certificados SSL: │
            │ • portal-crt.pem          │
            │ • portal-key.pem          │
            │ • portal-chain.pem        │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Cria pool de threads:     │
            │ ThreadsPerChild = 250     │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Apache RUNNING            │
            │ Aguardando conexões...    │
            │ Portas 80/443 abertas ✓   │
            └───────────┬───────────────┘
                        │
                        │
                        ▼
            ┌───────────────────────────┐
            │ MySQL Service             │
            │ Tipo: Automático          │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ mysqld.exe                │
            │ Bind em: 127.0.0.1:3306   │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Carrega banco de dados:   │
            │ cadastro_empresas_teste   │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ MySQL RUNNING             │
            │ Porta 3306 aberta ✓       │
            └───────────────────────────┘


                    ╔═══════════════════════════════╗
                    ║  INICIALIZAÇÃO MANUAL FLASK   ║
                    ╚═══════════════════════════════╝
                                 │
                                 ▼
            ┌───────────────────────────┐
            │ Usuário/Desenvolvedor:    │
            │ cd c:\Users\ti02\Desktop\ │
            │    site-teste             │
            │ python run.py             │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Python carrega .env       │
            │ • WAITRESS_PORT=9000      │
            │ • WAITRESS_THREADS=32     │
            │ • DB_HOST=localhost       │
            │ • SECRET_KEY=...          │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ run.py executa:           │
            │ app = create_app()        │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Flask inicializa:         │
            │ • SQLAlchemy              │
            │ • Flask-Login             │
            │ • Flask-WTF (CSRF)        │
            │ • Flask-Caching           │
            │ • Flask-Limiter           │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Registra Blueprints:      │
            │ • core_bp (/)             │
            │ • auth_bp (/login)        │
            │ • tasks_bp (/tasks)       │
            │ • api_bp (/api/v1)        │
            │ • ... (20 blueprints)     │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Testa conexão MySQL:      │
            │ db.create_all() se dev    │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Waitress inicia:          │
            │ serve(app,                │
            │   host='127.0.0.1',       │
            │   port=9000,              │
            │   threads=32              │
            │ )                         │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Waitress bind em:         │
            │ 127.0.0.1:9000            │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Cria pool de threads: 32  │
            └───────────┬───────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ Flask RUNNING             │
            │ Aguardando requisições    │
            │ via Apache proxy...       │
            └───────────┬───────────────┘
                        │
                        ▼
        ┌───────────────────────────────────┐
        │    SISTEMA TOTALMENTE OPERACIONAL │
        │                                   │
        │ ✓ Apache: Portas 80/443          │
        │ ✓ Flask: Porta 9000 (localhost)  │
        │ ✓ MySQL: Porta 3306 (localhost)  │
        │                                   │
        │ Pronto para receber requisições! │
        └───────────────────────────────────┘
```

---

## 4. Fluxograma de Tratamento de Erros

### Diagnóstico e Resolução de Problemas

```
                        ┌─────────────────┐
                        │ USUÁRIO REPORTA │
                        │     ERRO        │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Qual o sintoma?            │
                    └────────┬───────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│ Site não abre  │  │ Erro 502/504   │  │ Erro 500       │
│ (não carrega)  │  │ Bad Gateway    │  │ Internal Error │
└────────┬───────┘  └────────┬───────┘  └────────┬───────┘
         │                   │                   │
         ▼                   ▼                   ▼
┌────────────────────────────────────────────────────────┐
│                 ERRO: SITE NÃO ABRE                    │
└────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Apache está rodando?       │
│ sc query Apache2.4         │
└────────┬───────────────────┘
         │
    ┌────┴────┐
    │         │
  SIM        NÃO
    │         │
    │         ▼
    │  ┌──────────────────────┐
    │  │ Iniciar Apache:      │
    │  │ net start Apache2.4  │
    │  └──────────┬───────────┘
    │             │
    │             ▼
    │  ┌──────────────────────┐
    │  │ Iniciou com sucesso? │
    │  └──────────┬───────────┘
    │             │
    │        ┌────┴────┐
    │        │         │
    │       SIM       NÃO
    │        │         │
    │        │         ▼
    │        │  ┌──────────────────────┐
    │        │  │ Ver error log:       │
    │        │  │ C:\xampp\apache\     │
    │        │  │ logs\error.log       │
    │        │  └──────────┬───────────┘
    │        │             │
    │        │             ▼
    │        │  ┌──────────────────────┐
    │        │  │ Problemas comuns:    │
    │        │  │ • Porta em uso       │
    │        │  │ • Config inválida    │
    │        │  │ • DLLs faltando      │
    │        │  └──────────────────────┘
    │        │
    │        └─────────┬────────
    │                  │
    ▼                  ▼
┌──────────────────────────────┐
│ Portas 80/443 abertas?       │
│ netstat -ano | findstr :80   │
│ netstat -ano | findstr :443  │
└────────┬─────────────────────┘
         │
    ┌────┴────┐
    │         │
   SIM       NÃO
    │         │
    │         ▼
    │  ┌──────────────────────┐
    │  │ Porta bloqueada por: │
    │  │ • Firewall           │
    │  │ • Outro processo     │
    │  │ Corrigir e reiniciar │
    │  └──────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│ DNS resolvendo corretamente? │
│ nslookup portal.jpcontabil   │
│ .com.br                      │
└────────┬─────────────────────┘
         │
    ┌────┴────┐
    │         │
   SIM       NÃO
    │         │
    │         ▼
    │  ┌──────────────────────┐
    │  │ Problema de DNS:     │
    │  │ Aguardar propagação  │
    │  │ ou verificar registro│
    │  └──────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│ Certificado SSL válido?      │
│ openssl x509 -in cert.pem... │
└────────┬─────────────────────┘
         │
    ┌────┴────┐
    │         │
   SIM       NÃO
    │         │
    │         ▼
    │  ┌──────────────────────┐
    │  │ Renovar certificado: │
    │  │ certbot renew        │
    │  └──────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│ Problema resolvido ou        │
│ escalar para suporte técnico │
└──────────────────────────────┘


┌────────────────────────────────────────────────────────┐
│              ERRO 502: BAD GATEWAY                     │
└────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Flask está rodando?        │
│ netstat -ano | findstr :900│
└────────┬───────────────────┘
         │
    ┌────┴────┐
    │         │
   SIM       NÃO
    │         │
    │         ▼
    │  ┌──────────────────────┐
    │  │ Iniciar Flask:       │
    │  │ cd site-teste        │
    │  │ python run.py        │
    │  └──────────┬───────────┘
    │             │
    │             ▼
    │  ┌──────────────────────┐
    │  │ Verificar logs Flask │
    │  │ para erros de start  │
    │  └──────────────────────┘
    │
    ▼
┌────────────────────────────┐
│ Porta do ProxyPass correta?│
│ Verificar httpd-vhosts.conf│
│ ProxyPass / http://127.0.0│
│ .1:9000/ (não :5000!)      │
└────────┬───────────────────┘
         │
    ┌────┴────┐
    │         │
  CORRETA   ERRADA
    │         │
    │         ▼
    │  ┌──────────────────────┐
    │  │ Corrigir para :9000  │
    │  │ httpd -k graceful    │
    │  └──────────────────────┘
    │
    ▼
┌────────────────────────────┐
│ Firewall bloqueando        │
│ localhost:9000?            │
│ Testar: curl http://       │
│ localhost:9000/health      │
└────────┬───────────────────┘
         │
    ┌────┴────┐
    │         │
  FUNCIONA  BLOQUEADO
    │         │
    │         ▼
    │  ┌──────────────────────┐
    │  │ Adicionar exceção no │
    │  │ firewall para :9000  │
    │  └──────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│ Problema resolvido           │
└──────────────────────────────┘


┌────────────────────────────────────────────────────────┐
│              ERRO 500: INTERNAL ERROR                  │
└────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Ver logs da aplicação Flask│
│ c:\Users\ti02\Desktop\     │
│ site-teste\logs\app.log    │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Identificar erro:          │
│ • Erro de Python           │
│ • Query SQL inválida       │
│ • Módulo faltando          │
│ • Variável de ambiente     │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Corrigir código ou config  │
│ Reiniciar Flask            │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Testar novamente           │
└────────────────────────────┘
```

---

## 5. Fluxograma de API REST

### Requisição à API com Bearer Token

```
                        ┌─────────────────┐
                        │   INÍCIO        │
                        │ Cliente API faz │
                        │  requisição     │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ POST /api/v1/auth/login    │
                    │ Body: {                    │
                    │   "email": "user@...",     │
                    │   "password": "..."        │
                    │ }                          │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Flask valida credenciais   │
                    └────────────┬───────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                 VÁLIDAS                  INVÁLIDAS
                    │                         │
                    │                         ▼
                    │            ┌────────────────────────┐
                    │            │ Retorna 401            │
                    │            │ {"error": "Invalid..."}│
                    │            └────────────────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Gera JWT token (24h):      │
       │ token = jwt.encode({       │
       │   "user_id": 42,           │
       │   "exp": time() + 86400    │
       │ }, SECRET_KEY)             │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Retorna 200 OK:            │
       │ {                          │
       │   "token": "eyJhbGci...",  │
       │   "expires_in": 86400      │
       │ }                          │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Cliente armazena token     │
       └────────────┬───────────────┘
                    │
                    │
                    ▼
       ┌────────────────────────────┐
       │ Cliente faz requisição     │
       │ autenticada:               │
       │                            │
       │ GET /api/v1/tasks          │
       │ Authorization: Bearer      │
       │   eyJhbGci...              │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Flask extrai header:       │
       │ auth_header = request      │
       │   .headers.get(            │
       │     'Authorization'        │
       │   )                        │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Header presente?           │
       └────────────┬───────────────┘
                    │
       ┌────────────┴────────────┐
       │                         │
      SIM                       NÃO
       │                         │
       │                         ▼
       │            ┌────────────────────────┐
       │            │ Retorna 401            │
       │            │ {"error": "No token"}  │
       │            └────────────────────────┘
       │
       ▼
┌──────────────────────────┐
│ Valida formato:          │
│ "Bearer <token>"         │
└──────────┬───────────────┘
           │
      ┌────┴────┐
      │         │
   VÁLIDO    INVÁLIDO
      │         │
      │         ▼
      │  ┌──────────────────────┐
      │  │ Retorna 401          │
      │  │ {"error": "Invalid   │
      │  │  token format"}      │
      │  └──────────────────────┘
      │
      ▼
┌──────────────────────────┐
│ Decodifica JWT:          │
│ payload = jwt.decode(    │
│   token,                 │
│   SECRET_KEY,            │
│   algorithms=['HS256']   │
│ )                        │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Token válido?            │
│ (não expirado, assinatura│
│  correta)                │
└──────────┬───────────────┘
           │
      ┌────┴────┐
      │         │
   VÁLIDO    INVÁLIDO
      │         │
      │         ▼
      │  ┌──────────────────────┐
      │  │ Retorna 401          │
      │  │ {"error": "Token     │
      │  │  expired/invalid"}   │
      │  └──────────────────────┘
      │
      ▼
┌──────────────────────────┐
│ Carrega usuário:         │
│ user = User.query.get(   │
│   payload['user_id']     │
│ )                        │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Usuário existe e ativo?  │
└──────────┬───────────────┘
           │
      ┌────┴────┐
      │         │
     SIM       NÃO
      │         │
      │         ▼
      │  ┌──────────────────────┐
      │  │ Retorna 401          │
      │  │ {"error": "User not  │
      │  │  found/inactive"}    │
      │  └──────────────────────┘
      │
      ▼
┌──────────────────────────┐
│ Injeta current_user      │
│ g.current_user = user    │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Executa lógica da rota:  │
│ @api_bp.route('/tasks')  │
│ def get_tasks():         │
│   tasks = Task.query...  │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Retorna 200 OK:          │
│ {                        │
│   "tasks": [...]         │
│ }                        │
└──────────┬───────────────┘
           │
           ▼
    ┌─────────────┐
    │     FIM     │
    └─────────────┘
```

---

## 6. Fluxograma de Renovação SSL

### Processo de Renovação de Certificado Let's Encrypt

```
                        ┌─────────────────┐
                        │ INÍCIO          │
                        │ (Agendado ou    │
                        │  manual)        │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Verificar dias restantes:  │
                    │ openssl x509 -enddate ...  │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Dias < 30?                 │
                    └────────────┬───────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                   SIM                       NÃO
                    │                         │
                    │                         ▼
                    │            ┌────────────────────────┐
                    │            │ Log: "Certificado OK"  │
                    │            │ FIM                    │
                    │            └────────────────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Parar Apache:              │
       │ net stop Apache2.4         │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Certbot renew:             │
       │ certbot renew --standalone │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Certbot conecta em:        │
       │ acme-v02.api.letsencrypt   │
       │ .org                       │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Valida domínio (HTTP-01):  │
       │ 1. Certbot cria arquivo    │
       │    em .well-known/acme-    │
       │    challenge/              │
       │ 2. Let's Encrypt acessa:   │
       │    http://portal.jp...     │
       │    /.well-known/acme-...   │
       │ 3. Valida conteúdo         │
       └────────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────────┐
       │ Validação OK?              │
       └────────────┬───────────────┘
                    │
       ┌────────────┴────────────┐
       │                         │
      SIM                       NÃO
       │                         │
       │                         ▼
       │            ┌────────────────────────┐
       │            │ ERRO: Validação falhou │
       │            │ Possíveis causas:      │
       │            │ • DNS incorreto        │
       │            │ • Firewall bloqueando  │
       │            │ • Apache não parado    │
       │            │                        │
       │            │ Enviar alerta email    │
       │            │ FIM                    │
       │            └────────────────────────┘
       │
       ▼
┌──────────────────────────┐
│ Let's Encrypt gera novo  │
│ certificado (90 dias)    │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Certbot baixa arquivos:  │
│ • fullchain.pem          │
│ • privkey.pem            │
│ • chain.pem              │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Certbot salva em:        │
│ C:\Certbot\live\         │
│ portal.jpcontabil...     │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Copiar para local usado  │
│ pelo Apache:             │
│ copy fullchain.pem       │
│   C:\Certificados\...    │
│   -crt.pem               │
│ copy privkey.pem ...     │
│ copy chain.pem ...       │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Verificar novo cert:     │
│ openssl x509 -enddate... │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Iniciar Apache:          │
│ net start Apache2.4      │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Apache iniciou OK?       │
└──────────┬───────────────┘
           │
      ┌────┴────┐
      │         │
     SIM       NÃO
      │         │
      │         ▼
      │  ┌──────────────────────┐
      │  │ ERRO: Apache falhou  │
      │  │ Rollback certificado │
      │  │ anterior             │
      │  │ Enviar alerta        │
      │  └──────────────────────┘
      │
      ▼
┌──────────────────────────┐
│ Testar HTTPS:            │
│ curl https://portal.jp...|
│ Verificar novo cert no   │
│ navegador                │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Log: "Renovação OK"      │
│ Próxima renovação:       │
│ [data + 60 dias]         │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ Enviar email de sucesso  │
│ (opcional)               │
└──────────┬───────────────┘
           │
           ▼
    ┌─────────────┐
    │     FIM     │
    └─────────────┘
```

---

## 7. Fluxograma de Backup

### Processo Automatizado de Backup

```
                        ┌─────────────────┐
                        │ INÍCIO          │
                        │ Task Scheduler  │
                        │ (Domingo 2:00)  │
                        └────────┬────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Script PowerShell:         │
                    │ backup-apache.ps1          │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Gerar timestamp:           │
                    │ $date = Get-Date -Format   │
                    │   "yyyy-MM-dd_HHmmss"      │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Criar pasta de backup:     │
                    │ C:\backups\apache\         │
                    │   2026-02-13_020000        │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Copiar configurações:      │
                    │ C:\xampp\apache\conf\      │
                    │   → backup\conf\           │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Copiar certificados:       │
                    │ C:\Certificados\           │
                    │   → backup\certificados\   │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Criar arquivo info.txt:    │
                    │ • Data/hora                │
                    │ • Versão Apache            │
                    │ • Computador               │
                    │ • Usuário                  │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Comprimir backup:          │
                    │ Compress-Archive -Path     │
                    │   backup\ -Destination     │
                    │   backup.zip               │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Remover pasta descomprimida│
                    │ (manter apenas .zip)       │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Limpar backups antigos:    │
                    │ Remover backups com mais   │
                    │ de 30 dias                 │
                    └────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Log: "Backup concluído"    │
                    │ Tamanho: X MB              │
                    │ Local: C:\backups\...zip   │
                    └────────────┬───────────────┘
                                 │
                                 ▼
            ┌───────────────────────────────────┐
            │ BACKUP DO BANCO DE DADOS          │
            │ (Processo separado)               │
            └───────────────┬───────────────────┘
                            │
                            ▼
            ┌───────────────────────────────────┐
            │ mysqldump:                        │
            │ mysqldump -u root -p              │
            │   cadastro_empresas_teste         │
            │   > backup_YYYY-MM-DD.sql         │
            └───────────────┬───────────────────┘
                            │
                            ▼
            ┌───────────────────────────────────┐
            │ Comprimir SQL:                    │
            │ Compress-Archive backup.sql       │
            │   -Destination backup.sql.zip     │
            └───────────────┬───────────────────┘
                            │
                            ▼
            ┌───────────────────────────────────┐
            │ Copiar para NAS/Nuvem (opcional): │
            │ robocopy C:\backups\ \\NAS\...    │
            └───────────────┬───────────────────┘
                            │
                            ▼
            ┌───────────────────────────────────┐
            │ Enviar email de confirmação       │
            │ (opcional)                        │
            └───────────────┬───────────────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │     FIM     │
                     └─────────────┘
```

---

## 8. Arquitetura de Camadas

### Visão Completa do Sistema em Camadas

```
╔═══════════════════════════════════════════════════════════════════════╗
║                        CAMADA DE APRESENTAÇÃO                         ║
║                           (Cliente/Browser)                           ║
╚═══════════════════════════════════════════════════════════════════════╝
                                    │
                                    │ HTTPS (TLS 1.3)
                                    │ Porta 443
                                    │ Criptografia: AES-256-GCM
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════════╗
║                          CAMADA DE SEGURANÇA                          ║
║                       (SSL/TLS Termination)                           ║
║                                                                       ║
║  • Handshake SSL/TLS                                                 ║
║  • Validação de certificado (Let's Encrypt)                          ║
║  • Descriptografia                                                   ║
║  • Headers de segurança (HSTS, X-Frame-Options)                      ║
╚═══════════════════════════════════════════════════════════════════════╝
                                    │
                                    │ HTTP (descriptografado)
                                    │ Localhost interno
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════════╗
║                         CAMADA DE PROXY REVERSO                       ║
║                          Apache HTTP Server                           ║
║                                                                       ║
║  RESPONSABILIDADES:                                                   ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ VirtualHost Routing │  │ Proxy Configuration │                   ║
║  │ • ServerName match  │  │ • ProxyPass         │                   ║
║  │ • SSL certificates  │  │ • ProxyPassReverse  │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Performance         │  │ Security            │                   ║
║  │ • GZIP compression  │  │ • Request timeout   │                   ║
║  │ • Browser caching   │  │ • Rate limiting     │                   ║
║  │ • KeepAlive         │  │ • Header filtering  │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Logging             │  │ Static Files        │                   ║
║  │ • access.log        │  │ • CSS/JS/Images     │                   ║
║  │ • error.log         │  │ • Fonts             │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
╚═══════════════════════════════════════════════════════════════════════╝
                                    │
                                    │ HTTP (localhost)
                                    │ http://127.0.0.1:9000
                                    │ Headers: X-Forwarded-*
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════════╗
║                      CAMADA DE SERVIDOR WSGI                          ║
║                         Waitress Server                               ║
║                                                                       ║
║  RESPONSABILIDADES:                                                   ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Threading           │  │ Proxy Validation    │                   ║
║  │ • Pool: 32 threads  │  │ • Trusted proxy     │                   ║
║  │ • Concurrency       │  │ • Header validation │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ HTTP Parsing        │  │ WSGI Protocol       │                   ║
║  │ • Request parsing   │  │ • environ dict      │                   ║
║  │ • Response building │  │ • start_response    │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Connection Mgmt     │  │ Buffers/Timeouts    │                   ║
║  │ • Limit: 256 conns  │  │ • Channel: 100s     │                   ║
║  │ • Backlog: 256      │  │ • TCP: 32KB         │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
╚═══════════════════════════════════════════════════════════════════════╝
                                    │
                                    │ WSGI environ
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════════╗
║                        CAMADA DE APLICAÇÃO                            ║
║                          Flask Framework                              ║
║                                                                       ║
║  ┌─────────────────────────────────────────────────────────────────┐ ║
║  │                       REQUEST PIPELINE                          │ ║
║  │                                                                 │ ║
║  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐ │ ║
║  │  │   Router   │→ │  Security  │→ │    View    │→ │ Template │ │ ║
║  │  │  Matching  │  │  Middlewares│  │  Function  │  │ Render   │ │ ║
║  │  └────────────┘  └────────────┘  └────────────┘  └──────────┘ │ ║
║  │       ↓               ↓               ↓               ↓         │ ║
║  │  URL Pattern    Flask-Login    Business Logic   Jinja2 HTML    │ ║
║  │  /tasks         CSRF Token     def tasks()      tasks.html     │ ║
║  │  Blueprint      Rate Limit     Query DB         + data         │ ║
║  └─────────────────────────────────────────────────────────────────┘ ║
║                                                                       ║
║  COMPONENTES PRINCIPAIS:                                              ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Blueprints (20)     │  │ Extensions          │                   ║
║  │ • core_bp (/)       │  │ • Flask-Login       │                   ║
║  │ • auth_bp (/login)  │  │ • Flask-WTF (CSRF)  │                   ║
║  │ • tasks_bp (/tasks) │  │ • Flask-Caching     │                   ║
║  │ • api_bp (/api/v1)  │  │ • Flask-Limiter     │                   ║
║  │ • ... (16 mais)     │  │ • Flask-SQLAlchemy  │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Autenticação        │  │ Autorização         │                   ║
║  │ • Email/Senha       │  │ • Permissões        │                   ║
║  │ • Google OAuth 2.0  │  │ • Roles (admin...)  │                   ║
║  │ • Sessions          │  │ • @login_required   │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ API REST            │  │ Real-time           │                   ║
║  │ • Bearer Token      │  │ • SSE (notifications│                   ║
║  │ • JWT (24h)         │  │ • EventSource       │                   ║
║  │ • JSON responses    │  │ • Channel timeout   │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
╚═══════════════════════════════════════════════════════════════════════╝
                                    │
                                    │ SQLAlchemy ORM
                                    │ SQL Queries
                                    │
                                    ▼
╔═══════════════════════════════════════════════════════════════════════╗
║                          CAMADA DE DADOS                              ║
║                         MySQL Database                                ║
║                                                                       ║
║  TABELAS PRINCIPAIS:                                                  ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ users               │  │ tasks               │                   ║
║  │ • id (PK)           │  │ • id (PK)           │                   ║
║  │ • email (unique)    │  │ • title             │                   ║
║  │ • password_hash     │  │ • status            │                   ║
║  │ • name              │  │ • user_id (FK)      │                   ║
║  │ • is_active         │  │ • created_at        │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ empresas            │  │ meetings            │                   ║
║  │ • id (PK)           │  │ • id (PK)           │                   ║
║  │ • nome              │  │ • title             │                   ║
║  │ • cnpj              │  │ • start_time        │                   ║
║  │ • setor_id (FK)     │  │ • google_event_id   │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  RECURSOS:                                                            ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Transações ACID     │  │ Índices             │                   ║
║  │ • BEGIN/COMMIT      │  │ • PRIMARY KEY       │                   ║
║  │ • ROLLBACK          │  │ • INDEX user_id     │                   ║
║  │ • Isolation levels  │  │ • UNIQUE email      │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
║                                                                       ║
║  ┌─────────────────────┐  ┌─────────────────────┐                   ║
║  │ Connection Pool     │  │ Backup              │                   ║
║  │ • Pool size: 10     │  │ • mysqldump diário  │                   ║
║  │ • Max overflow: 5   │  │ • Retenção: 30 dias │                   ║
║  │ • Pre-ping: True    │  │ • Compressão: zip   │                   ║
║  └─────────────────────┘  └─────────────────────┘                   ║
╚═══════════════════════════════════════════════════════════════════════╝
                                    │
                                    │ Resultados
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │  RESPOSTA RETORNA     │
                        │  PELO MESMO CAMINHO   │
                        │  (MySQL → Flask →     │
                        │   Waitress → Apache → │
                        │   Cliente)            │
                        └───────────────────────┘
```

### Fluxo de Dados (Visão Simplificada)

```
REQUEST  ───────────────────────────────────────────────────────────→

┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│ Cliente │────▶│ Apache  │────▶│Waitress │────▶│  Flask  │────▶│  MySQL  │
│ Browser │     │ (Proxy) │     │ (WSGI)  │     │  (App)  │     │  (DB)   │
└─────────┘     └─────────┘     └─────────┘     └─────────┘     └─────────┘
   HTTPS           HTTP            WSGI           Python           SQL
  (443)          (local)         (environ)       (objects)       (queries)

  SSL/TLS      Proxy headers    Threading     Business logic   Persistence
Compression   X-Forwarded-*     Pool mgmt     Authn/Authz      Transactions
  Caching      Load balance    Connection     Validation        Indexes
  Logging       Timeouts         Buffers       Templates         Joins

←─────────────────────────────────────────────────────────────── RESPONSE

          HTML/JSON ← Render ← Objects ← Rows ← SELECT
```

---

## Conclusão

Estes fluxogramas fornecem uma visão completa e detalhada de todos os processos críticos do sistema Portal JP Contábil, desde o boot do Windows até o tratamento de erros e backups.

**Documentos relacionados:**
- [PROXY_REVERSO.md](PROXY_REVERSO.md) - Documentação técnica completa
- [MANUTENCAO_APACHE.md](MANUTENCAO_APACHE.md) - Guia de manutenção
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Referência rápida

---

**Última atualização:** Fevereiro 2026
**Versão do portal:** v2.0.4
