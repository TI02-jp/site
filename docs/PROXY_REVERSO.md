# Documentação: Arquitetura de Proxy Reverso Apache + Flask

**Portal JP Contábil**
**Versão:** 2.0.4
**Data:** Fevereiro 2026

---

## Índice

1. [Introdução ao Proxy Reverso](#1-introdução-ao-proxy-reverso)
2. [Arquitetura do Sistema](#2-arquitetura-do-sistema)
3. [Por Que Apache nas Portas 80/443?](#3-por-que-apache-nas-portas-80443)
4. [Por Que Python na Porta 9000 (Localhost)?](#4-por-que-python-na-porta-9000-localhost)
5. [Fluxo Detalhado de uma Requisição](#5-fluxo-detalhado-de-uma-requisição)
6. [Configuração do Serviço Windows](#6-configuração-do-serviço-windows)
7. [Configurações Críticas do Apache](#7-configurações-críticas-do-apache)
8. [Configurações Críticas do Flask](#8-configurações-críticas-do-flask)
9. [Redirecionamento HTTP para HTTPS](#9-redirecionamento-http-para-https)
10. [Certificados SSL](#10-certificados-ssl)
11. [Segurança](#11-segurança)
12. [Performance](#12-performance)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Introdução ao Proxy Reverso

### O que é um Proxy Reverso?

Um **proxy reverso** é um servidor que fica entre clientes (navegadores) e servidores backend (aplicações). Ele recebe requisições de clientes e as encaminha para servidores internos, retornando as respostas aos clientes.

```
Cliente → Proxy Reverso → Servidor Backend
Cliente ← Proxy Reverso ← Servidor Backend
```

### Diferença entre Proxy Direto e Proxy Reverso

| Aspecto | Proxy Direto (Forward Proxy) | Proxy Reverso (Reverse Proxy) |
|---------|------------------------------|-------------------------------|
| **Posição** | Entre cliente e internet | Entre internet e servidor |
| **Propósito** | Proteger/controlar clientes | Proteger/otimizar servidores |
| **Exemplo** | VPN corporativa | Apache, Nginx, Cloudflare |
| **Quem sabe dele?** | Cliente sabe | Cliente não sabe (transparente) |

### Vantagens de Usar Proxy Reverso

1. **Segurança:**
   - Servidor backend isolado da internet (localhost)
   - SSL/TLS terminado no proxy (proteção de tráfego)
   - Proteção contra ataques DDoS
   - Ocultação da infraestrutura interna

2. **Performance:**
   - Cache de conteúdo estático
   - Compressão GZIP/Brotli
   - Conexões persistentes (KeepAlive)
   - Load balancing (distribuição de carga)

3. **Flexibilidade:**
   - Múltiplas aplicações em um único IP
   - Diferentes tecnologias backend (Python, Node.js, PHP)
   - Fácil manutenção de certificados SSL
   - Logging centralizado

4. **Escalabilidade:**
   - Adicionar servidores backend sem mudar DNS
   - Balanceamento de carga entre múltiplos backends
   - Alta disponibilidade (failover)

---

## 2. Arquitetura do Sistema

### Diagrama de Fluxo de Requisição

```
┌─────────────────────────────────────────────────────────────────────┐
│                          INTERNET                                   │
│                     (Acesso Público)                                │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ HTTPS (porta 443)
                                 │ https://portal.jpcontabil.com.br
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    APACHE HTTP SERVER 2.4.58                        │
│                         (Proxy Reverso)                             │
│                   Portas: 80 (HTTP) + 443 (HTTPS)                   │
│                                                                     │
│  RESPONSABILIDADES:                                                 │
│  ✓ Terminar SSL/TLS (descriptografar HTTPS)                        │
│  ✓ Validar certificado Let's Encrypt                               │
│  ✓ Aplicar headers de segurança                                    │
│  ✓ Compressão GZIP de respostas                                    │
│  ✓ Cache de arquivos estáticos (CSS, JS, imagens)                  │
│  ✓ Adicionar headers X-Forwarded-* (Proto, For, Host)             │
│  ✓ Logs de acesso e erros                                          │
│                                                                     │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ HTTP (sem SSL, tráfego local)
                                 │ http://127.0.0.1:9000
                                 │ Headers: X-Forwarded-Proto: https
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   WAITRESS WSGI SERVER 3.0.2                        │
│                      Porta: 9000 (localhost only)                   │
│                                                                     │
│  RESPONSABILIDADES:                                                 │
│  ✓ Receber requisições HTTP do Apache                              │
│  ✓ Gerenciar pool de 32 threads                                    │
│  ✓ Gerenciar conexões persistentes                                 │
│  ✓ Passar requisições para Flask via WSGI                          │
│  ✓ Retornar respostas HTTP para Apache                             │
│                                                                     │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ WSGI Protocol
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   FLASK APPLICATION 3.1.0                           │
│                     (Portal JP Contábil)                            │
│                                                                     │
│  RESPONSABILIDADES:                                                 │
│  ✓ Roteamento de URLs (Blueprints)                                 │
│  ✓ Autenticação (Flask-Login + Google OAuth)                       │
│  ✓ Proteção CSRF (Flask-WTF)                                       │
│  ✓ Lógica de negócio (tarefas, empresas, usuários)                │
│  ✓ Renderização de templates (Jinja2)                              │
│  ✓ API RESTful (/api/v1)                                           │
│  ✓ Server-Sent Events (SSE) para notificações                      │
│                                                                     │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ SQL Queries
                                 │ mysql+mysqlconnector://
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   MYSQL DATABASE SERVER                             │
│                 Database: cadastro_empresas_teste                   │
│                      Porta: 3306 (localhost)                        │
│                                                                     │
│  RESPONSABILIDADES:                                                 │
│  ✓ Persistência de dados                                           │
│  ✓ Consultas SQL (SELECT, INSERT, UPDATE, DELETE)                  │
│  ✓ Transações ACID                                                 │
│  ✓ Relacionamentos entre tabelas                                   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

[Resposta segue o caminho inverso: MySQL → Flask → Waitress → Apache → Cliente]
```

### Diagrama de Segurança em Camadas

```
┌─────────────────────────────────────────────────────────────────────┐
│  CAMADA 1: INTERNET (Zona Pública)                                 │
│  ─────────────────────────────────────────────────────────────────  │
│  • Origem: Qualquer IP externo                                     │
│  • Ameaças: DDoS, SQL Injection, XSS, CSRF, Bots                   │
│  • Exposição: Total (acessível publicamente)                       │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ Firewall / Router / Porta 443
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAMADA 2: APACHE (Zona DMZ - DeMilitarized Zone)                  │
│  ─────────────────────────────────────────────────────────────────  │
│  • DEFESAS ATIVAS:                                                 │
│    ✓ SSL/TLS Termination (criptografia forte RSA 3072-bit)        │
│    ✓ Validação de certificados (Let's Encrypt)                    │
│    ✓ Timeout de requisições (30-60s headers, 300s proxy)          │
│    ✓ Rate limiting (mod_ratelimit - se configurado)               │
│    ✓ Headers de segurança (X-Frame-Options, CSP)                  │
│    ✓ Logs detalhados (access.log, error.log)                      │
│                                                                     │
│  • FILTRAGEM:                                                      │
│    ✓ Apenas proxy para localhost (127.0.0.1:9000)                 │
│    ✓ ProxyPass com retry=0 (sem propagação de falhas)             │
│    ✓ RequestReadTimeout com MinRate (anti-slowloris)              │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ HTTP localhost (127.0.0.1:9000)
                                 │ ⚠️ NÃO ACESSÍVEL EXTERNAMENTE ⚠️
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAMADA 3: FLASK/WAITRESS (Zona Privada)                           │
│  ─────────────────────────────────────────────────────────────────  │
│  • ISOLAMENTO:                                                     │
│    ✓ Bind apenas em 127.0.0.1 (não em 0.0.0.0)                    │
│    ✓ Porta 9000 (inacessível de redes externas)                   │
│    ✓ Trusted proxy validation (127.0.0.1)                         │
│                                                                     │
│  • DEFESAS ATIVAS:                                                 │
│    ✓ CSRF Protection (Flask-WTF com tokens únicos)                │
│    ✓ Autenticação (Flask-Login + sessions)                        │
│    ✓ Autorização (permissões por usuário/role)                    │
│    ✓ Rate limiting (Flask-Limiter por IP/usuário)                 │
│    ✓ Input validation (SQLAlchemy ORM, sanitização)               │
│    ✓ Output encoding (Jinja2 auto-escaping)                       │
│    ✓ Secure cookies (HttpOnly, Secure, SameSite)                  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ MySQL localhost (127.0.0.1:3306)
                                 │ ⚠️ NÃO ACESSÍVEL EXTERNAMENTE ⚠️
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAMADA 4: MYSQL DATABASE (Zona de Dados)                          │
│  ─────────────────────────────────────────────────────────────────  │
│  • ISOLAMENTO:                                                     │
│    ✓ Bind apenas em localhost (127.0.0.1:3306)                    │
│    ✓ Credenciais em .env (não hardcoded)                          │
│    ✓ Sem acesso remoto (skip-networking ou bind-address)          │
│                                                                     │
│  • DEFESAS:                                                        │
│    ✓ Usuário com privilégios limitados                            │
│    ✓ Backup automático regular                                    │
│    ✓ Prepared statements via SQLAlchemy (anti-SQL injection)      │
│    ✓ Logs de queries (slow query log)                             │
└─────────────────────────────────────────────────────────────────────┘
```

**Princípio de Defesa em Profundidade:**
Se um atacante comprometer uma camada, as outras continuam protegendo o sistema.

---

## 3. Por Que Apache nas Portas 80/443?

### Portas Padrão Web

- **Porta 80 (HTTP):** Porta padrão para tráfego web não criptografado
  - Navegadores acessam `http://site.com` automaticamente na porta 80
  - Não requer `:80` explícito na URL

- **Porta 443 (HTTPS):** Porta padrão para tráfego web criptografado (SSL/TLS)
  - Navegadores acessam `https://site.com` automaticamente na porta 443
  - Não requer `:443` explícito na URL

### Por Que NÃO Expor o Flask Diretamente?

Se rodássemos Flask diretamente na porta 443, teríamos os seguintes problemas:

#### ❌ Problema 1: Segurança SSL/TLS
- Flask/Waitress não são otimizados para terminação SSL
- Apache tem 20+ anos de maturidade em segurança SSL
- Apache recebe patches de segurança rapidamente
- Configuração SSL no Flask é complexa e propensa a erros

#### ❌ Problema 2: Privilégios Administrativos
- Portas < 1024 requerem privilégios de **administrador/root** no sistema operacional
- Rodar aplicação Python como administrador é **risco de segurança enorme**
- Se a aplicação for comprometida, atacante tem acesso root ao servidor
- Apache roda como serviço Windows (LocalSystem) controlado pelo SO

#### ❌ Problema 3: Performance
- Apache é **altamente otimizado** para:
  - Servir arquivos estáticos (CSS, JS, imagens) diretamente do disco
  - Compressão GZIP com módulos nativos em C
  - Cache de conteúdo (mod_cache)
  - Gerenciamento de conexões persistentes (KeepAlive)
- Flask/Waitress são bons para lógica de aplicação, não para servir estáticos

#### ❌ Problema 4: Flexibilidade
- Apache permite hospedar **múltiplas aplicações** em um único IP:
  - `portal.jpcontabil.com.br` → Flask (porta 9000)
  - `api.jpcontabil.com.br` → Node.js (porta 3000)
  - `blog.jpcontabil.com.br` → WordPress/PHP (pasta htdocs)
- Cada aplicação pode usar tecnologia diferente
- VirtualHosts permitem roteamento por domínio

#### ❌ Problema 5: Gerenciamento de Certificados
- Apache integra facilmente com Let's Encrypt (Certbot)
- Renovação automática de certificados
- Suporte a múltiplos certificados (SNI - Server Name Indication)
- Validação OCSP Stapling integrada

#### ❌ Problema 6: Isolamento de Falhas
- Se aplicação Flask crashar, Apache continua respondendo com erro 502
- Apache pode fazer health checks e failover automático
- Logs centralizados facilitam debugging

#### ❌ Problema 7: Logging e Auditoria
- Apache gera logs padronizados (Common Log Format, Combined Log Format)
- Ferramentas de análise (Webalizer, AWStats) funcionam nativamente
- Logs separados por VirtualHost
- Rotação de logs automática

### ✅ Solução: Apache como Proxy Reverso

Com Apache como proxy reverso, temos:

| Benefício | Descrição |
|-----------|-----------|
| **Segurança SSL** | Apache gerencia SSL com robustez comprovada |
| **Sem Privilégios Root** | Flask roda em porta alta (9000) como usuário normal |
| **Performance** | Apache serve estáticos, Flask foca em lógica |
| **Escalabilidade** | Adicionar backends sem mudar DNS |
| **Manutenção** | Renovação SSL sem restart da aplicação |
| **Monitoramento** | Logs centralizados e estruturados |
| **Load Balancing** | Possível distribuir carga entre múltiplos backends |
| **Cache** | mod_cache pode cachear respostas Flask |

---

## 4. Por Que Python na Porta 9000 (Localhost)?

### Porta Alta (> 1024)

- Portas acima de 1024 **não requerem privilégios de administrador**
- Aplicação Flask pode rodar como usuário normal (não-root)
- Menor superfície de ataque se a aplicação for comprometida
- Facilita desenvolvimento e testes locais

### Localhost (127.0.0.1)

- **127.0.0.1** é o endereço de loopback local (localhost)
- Tráfego em 127.0.0.1 **nunca sai da máquina**
- Inacessível de redes externas (LAN ou internet)
- Apenas processos locais (Apache) podem conectar

**Comparação:**
```python
# ❌ INSEGURO - Acessível de qualquer rede
serve(app, host='0.0.0.0', port=9000)

# ✅ SEGURO - Apenas localhost
serve(app, host='127.0.0.1', port=9000)
```

### Waitress WSGI Server

**Por que Waitress e não servidor de desenvolvimento Flask?**

| Servidor | Uso | Performance | Segurança | Threads |
|----------|-----|-------------|-----------|---------|
| Flask dev server | Desenvolvimento | Baixa | ❌ Inseguro | 1 thread |
| Waitress | Produção | Alta | ✅ Seguro | 32 threads |
| Gunicorn | Produção (Linux) | Alta | ✅ Seguro | Workers |
| uWSGI | Produção | Muito alta | ✅ Seguro | Workers |

**Vantagens do Waitress:**
- ✅ **Multiplataforma:** Funciona em Windows, Linux, macOS
- ✅ **Threads nativas:** Suporta concorrência real (não GIL-limited)
- ✅ **Estável:** Usado em produção por grandes empresas
- ✅ **Fácil configuração:** Não requer arquivos complexos (vs uWSGI)
- ✅ **Baixa latência:** Implementação eficiente em Python puro

### Separação de Responsabilidades

Cada camada foca em sua especialidade:

| Componente | Responsabilidade Principal |
|------------|----------------------------|
| **Apache** | SSL, proxy, estáticos, logs |
| **Waitress** | Threading, WSGI, HTTP parsing |
| **Flask** | Lógica de negócio, templates, ORM |
| **MySQL** | Persistência, consultas, transações |

**Analogia:** É como uma cozinha profissional:
- **Apache** = Garçom (atende clientes, serve pratos prontos)
- **Waitress** = Gerente de cozinha (organiza pedidos)
- **Flask** = Chef (prepara os pratos)
- **MySQL** = Despensa (armazena ingredientes)

---

## 5. Fluxo Detalhado de uma Requisição

### Exemplo Real: Usuário acessa `https://portal.jpcontabil.com.br/tasks`

#### Passo 1: Cliente → DNS
```
Cliente executa: curl https://portal.jpcontabil.com.br/tasks

1. Browser consulta DNS: "Qual o IP de portal.jpcontabil.com.br?"
2. DNS responde: "IP: xxx.xxx.xxx.xxx"
3. Browser conecta em xxx.xxx.xxx.xxx:443
```

#### Passo 2: Cliente → Apache (Handshake SSL/TLS)
```
1. Cliente envia: ClientHello (versões TLS suportadas, cipher suites)
2. Apache responde: ServerHello (TLS 1.3 escolhido, cipher escolhido)
3. Apache envia: Certificado Let's Encrypt (portal.jpcontabil.com.br-crt.pem)
4. Cliente valida:
   ✓ Certificado assinado por CA confiável (Let's Encrypt R13)
   ✓ Domínio no certificado = domínio requisitado
   ✓ Certificado não expirado (válido até 12/04/2026)
5. Cliente gera chave de sessão e criptografa com chave pública do servidor
6. Apache descriptografa com chave privada (portal.jpcontabil.com.br-key.pem)
7. ✅ Conexão criptografada estabelecida (HTTPS)
```

#### Passo 3: Apache → VirtualHost Matching
```
Requisição recebida:
  Host: portal.jpcontabil.com.br
  Path: /tasks
  Method: GET

Apache verifica VirtualHosts configurados:
  <VirtualHost *:443>
    ServerName portal.jpcontabil.com.br  ← ✅ MATCH!
  </VirtualHost>

Apache roteia para este VirtualHost.
```

#### Passo 4: Apache → Proxy Reverso
```
Configuração do VirtualHost:
  ProxyPass / http://127.0.0.1:9000/ retry=0 timeout=300
  ProxyPreserveHost On

Apache transforma requisição HTTPS em HTTP:
  Antes (HTTPS, do cliente):
    GET /tasks HTTP/1.1
    Host: portal.jpcontabil.com.br
    User-Agent: Mozilla/5.0
    Cookie: session=abc123

  Depois (HTTP, para Waitress):
    GET /tasks HTTP/1.1
    Host: portal.jpcontabil.com.br       ← preservado (ProxyPreserveHost On)
    User-Agent: Mozilla/5.0
    Cookie: session=abc123
    X-Forwarded-For: 200.150.100.50      ← IP do cliente original
    X-Forwarded-Proto: https             ← protocolo original
    X-Forwarded-Host: portal.jpcontabil.com.br

Apache envia para: http://127.0.0.1:9000/tasks
```

**Por que descriptografar SSL?**
- Apache já validou a identidade do cliente
- Tráfego localhost (127.0.0.1) não sai da máquina (seguro)
- Flask não precisa se preocupar com SSL (separação de responsabilidades)
- Performance: SSL/TLS é computacionalmente caro, fazer uma vez é suficiente

#### Passo 5: Waitress → Flask
```
Waitress recebe requisição HTTP em 127.0.0.1:9000

1. Waitress valida:
   ✓ Trusted proxy? Sim (127.0.0.1 configurado como confiável)
   ✓ Headers X-Forwarded-* válidos

2. Waitress processa headers:
   - Detecta X-Forwarded-Proto: https → request.is_secure = True
   - Passa User-Agent, Cookie, etc. para Flask

3. Waitress ativa thread do pool (1 de 32 disponíveis)

4. Thread chama Flask app via WSGI:
   environ = {
     'REQUEST_METHOD': 'GET',
     'PATH_INFO': '/tasks',
     'HTTP_HOST': 'portal.jpcontabil.com.br',
     'wsgi.url_scheme': 'https',  ← detectado via X-Forwarded-Proto
     ...
   }
   response = app(environ, start_response)
```

#### Passo 6: Flask Processa Requisição
```
1. Flask Router:
   URL: /tasks
   ↓
   Blueprint: tasks_bp
   ↓
   Route: @tasks_bp.route('/tasks')
   ↓
   Function: tasks_overview()

2. Flask-Login verifica autenticação:
   - Lê cookie 'session'
   - Valida session_id no servidor
   - Carrega usuário do banco (user = User.query.get(session['user_id']))
   - Injeta em current_user

3. CSRF Protection (Flask-WTF):
   - Verifica CSRF token (se for POST)

4. Lógica de negócio:
   def tasks_overview():
       # Consulta banco de dados
       tasks = Task.query.filter_by(user_id=current_user.id).all()

       # Renderiza template
       return render_template('tasks.html', tasks=tasks)

5. SQLAlchemy → MySQL:
   SELECT * FROM tasks WHERE user_id = 42;
```

#### Passo 7: MySQL → Resposta
```
MySQL processa query:
  SELECT * FROM tasks WHERE user_id = 42;

MySQL retorna:
  [
    {'id': 1, 'title': 'Revisar relatório', 'status': 'Em andamento'},
    {'id': 2, 'title': 'Reunião com cliente', 'status': 'Concluída'},
    ...
  ]

SQLAlchemy mapeia para objetos Python (Task instances)
```

#### Passo 8: Flask → Waitress → Apache
```
1. Flask renderiza template Jinja2:
   tasks.html + dados → HTML gerado

2. Flask retorna resposta:
   HTTP/1.1 200 OK
   Content-Type: text/html; charset=utf-8
   Content-Length: 15432
   Set-Cookie: session=abc123; HttpOnly; Secure; SameSite=Lax

   <!DOCTYPE html>
   <html>
   ...
   </html>

3. Waitress encapsula em HTTP response

4. Waitress envia para Apache (127.0.0.1:9000 → Apache)

5. Apache recebe HTTP response
```

#### Passo 9: Apache → Cliente
```
Apache processa resposta:

1. Compressão (mod_deflate):
   - HTML (15KB) → comprime para ~4KB (gzip)
   - Adiciona header: Content-Encoding: gzip

2. Headers de segurança:
   - X-Frame-Options: DENY
   - X-Content-Type-Options: nosniff
   - Strict-Transport-Security: max-age=31536000

3. Criptografia SSL:
   - Criptografa resposta completa com chave de sessão

4. Envia para cliente (HTTPS):
   HTTP/1.1 200 OK
   Content-Type: text/html; charset=utf-8
   Content-Encoding: gzip
   Content-Length: 4231
   X-Frame-Options: DENY
   ...

   [Dados HTML comprimidos e criptografados]
```

#### Passo 10: Cliente Renderiza
```
1. Browser descriptografa resposta HTTPS
2. Browser descomprime gzip (4KB → 15KB)
3. Browser parseia HTML
4. Browser renderiza página de tarefas
5. ✅ Usuário vê lista de tarefas
```

### Resumo do Fluxo

```
┌────────────┐      HTTPS         ┌────────┐     HTTP      ┌─────────┐
│  Cliente   │ ─────────────────→ │ Apache │ ────────────→ │ Waitress│
│  Browser   │                     │  :443  │               │  :9000  │
└────────────┘                     └────────┘               └─────────┘
      ▲                                 ▲                        │
      │                                 │                        │
      │                                 │                        ▼
      │                                 │                   ┌─────────┐
      │                                 │                   │  Flask  │
      │       HTML criptografado        │    HTTP Response  │   App   │
      │       (gzip comprimido)         │                   └─────────┘
      │                                 │                        │
      │                                 │                        │
      └─────────────────────────────────┘                        ▼
                                                            ┌─────────┐
                                                            │  MySQL  │
                                                            │  :3306  │
                                                            └─────────┘

Tempo total: ~100-300ms (depende da query no banco)
```

---

## 6. Configuração do Serviço Windows

### Como o Apache Roda como Serviço

O Apache no Windows é instalado como um **Serviço Windows** gerenciado pelo sistema operacional.

**Detalhes do Serviço:**
- **Nome do serviço:** `Apache2.4`
- **Nome de exibição:** Apache2.4
- **Tipo de inicialização:** Automática (inicia ao ligar o Windows)
- **Conta de execução:** LocalSystem (conta do sistema)
- **Dependências:** Tcpip, Afd (serviços de rede)
- **Binário:** `C:\xampp\apache\bin\httpd.exe -k runservice`

### Comandos de Gerenciamento do Serviço

#### Via Linha de Comando (CMD como Administrador)

**Instalar o serviço:**
```batch
cd C:\xampp\apache\bin
httpd.exe -k install
```

**Remover o serviço:**
```batch
cd C:\xampp\apache\bin
httpd.exe -k uninstall
```

**Iniciar o serviço:**
```batch
net start Apache2.4
```
Ou:
```batch
cd C:\xampp\apache\bin
httpd.exe -k start
```

**Parar o serviço:**
```batch
net stop Apache2.4
```
Ou:
```batch
cd C:\xampp\apache\bin
httpd.exe -k stop
```

**Reiniciar o serviço:**
```batch
cd C:\xampp\apache\bin
httpd.exe -k restart
```

**Verificar status do serviço:**
```batch
sc query Apache2.4
```

**Verificar se está rodando:**
```batch
netstat -ano | findstr :80
netstat -ano | findstr :443
```

#### Via Painel de Controle XAMPP

O **XAMPP Control Panel** (`C:\xampp\xampp-control.exe`) oferece interface gráfica:

1. **Abrir o painel:**
   ```batch
   C:\xampp\xampp-control.exe
   ```

2. **Iniciar Apache:**
   - Clicar no botão **Start** ao lado de Apache

3. **Parar Apache:**
   - Clicar no botão **Stop** ao lado de Apache

4. **Configurar autostart:**
   - Marcar checkbox na coluna **Autostart** da linha Apache
   - Apache iniciará automaticamente ao abrir o painel de controle

5. **Verificar logs:**
   - Clicar em **Logs** → **Apache (error.log)** ou **Apache (access.log)**

6. **Verificar portas:**
   - Botão **Netstat** mostra portas em uso

#### Via Gerenciador de Serviços Windows

1. **Abrir Serviços:**
   ```batch
   services.msc
   ```

2. **Localizar serviço:**
   - Procurar por **Apache2.4** na lista

3. **Gerenciar:**
   - Botão direito → **Iniciar** / **Parar** / **Reiniciar**
   - Botão direito → **Propriedades** → Aba **Geral**:
     - **Tipo de inicialização:** Automática / Manual / Desabilitada

### Scripts Batch Úteis

O XAMPP fornece scripts prontos:

**Iniciar Apache (modo console, não como serviço):**
```batch
C:\xampp\apache_start.bat
```
- Abre janela de console
- Apache roda em foreground
- Logs aparecem em tempo real
- **Não fechar a janela** enquanto Apache estiver em uso
- Útil para debugging

**Parar Apache (modo console):**
```batch
C:\xampp\apache_stop.bat
```

**Instalar como serviço:**
```batch
C:\xampp\apache\apache_installservice.bat
```

**Desinstalar serviço:**
```batch
C:\xampp\apache\apache_uninstallservice.bat
```

### Configuração de Autostart

**Método 1: Via registro do Windows**
```batch
# Verificar configuração atual
reg query HKLM\SYSTEM\CurrentControlSet\Services\Apache2.4 /v Start

# Configurar autostart (2 = automático)
reg add HKLM\SYSTEM\CurrentControlSet\Services\Apache2.4 /v Start /t REG_DWORD /d 2 /f

# Configurar start manual (3 = manual)
reg add HKLM\SYSTEM\CurrentControlSet\Services\Apache2.4 /v Start /t REG_DWORD /d 3 /f
```

**Método 2: Via XAMPP Control Panel**
- Arquivo: `C:\xampp\xampp-control.ini`
```ini
[Autostart]
Apache=1    ← 1 = autostart habilitado, 0 = desabilitado
MySQL=1
```

### Verificação de Status

**Verificar se Apache está rodando:**
```batch
# Via tasklist
tasklist | findstr httpd.exe

# Via netstat (verificar portas)
netstat -ano | findstr :80
netstat -ano | findstr :443

# Via sc query
sc query Apache2.4
```

**Saída esperada (Apache rodando):**
```
SERVICE_NAME: Apache2.4
        TYPE               : 10  WIN32_OWN_PROCESS
        STATE              : 4  RUNNING
        WIN32_EXIT_CODE    : 0  (0x0)
```

### Logs do Serviço

**Logs do Apache:**
- **Error log:** `C:\xampp\apache\logs\error.log`
- **Access log:** `C:\xampp\apache\logs\access.log`
- **SSL request log:** `C:\xampp\apache\logs\ssl_request.log`

**Logs do XAMPP Control Panel:**
- `C:\xampp\xampp-control.log`

**Visualizar logs em tempo real:**
```batch
# Via PowerShell
Get-Content C:\xampp\apache\logs\error.log -Wait -Tail 50

# Via CMD (com GNU tail, se instalado)
tail -f C:\xampp\apache\logs\error.log
```

### Troubleshooting: Serviço não Inicia

**Problema 1: Porta em uso**
```batch
# Verificar o que está usando a porta 80
netstat -ano | findstr :80

# Matar processo (substituir PID pelo número encontrado)
taskkill /PID 1234 /F
```

**Problema 2: Configuração inválida**
```batch
# Testar configuração antes de iniciar
C:\xampp\apache\bin\httpd.exe -t

# Saída esperada: "Syntax OK"
```

**Problema 3: Permissões**
- Executar CMD como **Administrador**
- Verificar se usuário tem permissão para iniciar serviços

**Problema 4: DLLs faltando**
- Instalar **Visual C++ Redistributable 2015-2022**
- Download: https://aka.ms/vs/17/release/vc_redist.x64.exe

---

## 7. Configurações Críticas do Apache

### Localização dos Arquivos de Configuração

```
C:\xampp\apache\conf\
├── httpd.conf                    ← Configuração principal
├── extra\
│   ├── httpd-vhosts.conf         ← VirtualHosts (proxy reverso configurado aqui)
│   ├── httpd-ssl.conf            ← Configurações SSL globais
│   ├── httpd-proxy.conf          ← Configurações de proxy
│   ├── httpd-default.conf        ← Timeouts e defaults
│   ├── httpd-mpm.conf            ← MPM (Multi-Processing Module)
│   └── httpd-xampp.conf          ← Configurações específicas XAMPP
└── ssl\                          ← Certificados (alternativos)
```

### httpd.conf - Configuração Principal

**Módulos críticos habilitados:**
```apache
# Proxy Modules
LoadModule proxy_module modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
LoadModule proxy_ajp_module modules/mod_proxy_ajp.so

# SSL/TLS
LoadModule ssl_module modules/mod_ssl.so
LoadModule socache_shmcb_module modules/mod_socache_shmcb.so

# Headers e Rewrite
LoadModule headers_module modules/mod_headers.so
LoadModule rewrite_module modules/mod_rewrite.so

# Compressão
LoadModule deflate_module modules/mod_deflate.so

# Cache
LoadModule expires_module modules/mod_expires.so
LoadModule cache_module modules/mod_cache.so
LoadModule cache_disk_module modules/mod_cache_disk.so
```

**Listening ports:**
```apache
Listen 80   # HTTP
```

**Includes:**
```apache
Include conf/extra/httpd-vhosts.conf    ← VirtualHosts
Include conf/extra/httpd-ssl.conf       ← SSL
Include conf/extra/httpd-default.conf   ← Timeouts
Include conf/extra/httpd-xampp.conf     ← XAMPP específico
```

**Compressão GZIP:**
```apache
<IfModule mod_deflate.c>
    AddOutputFilterByType DEFLATE text/html text/plain text/xml
    AddOutputFilterByType DEFLATE text/css text/javascript application/javascript
    AddOutputFilterByType DEFLATE application/json application/xml

    # Não comprimir imagens (já comprimidas)
    SetEnvIfNoCase Request_URI \.(?:gif|jpe?g|png|webp|ico)$ no-gzip
</IfModule>
```

**Cache de browser:**
```apache
<IfModule mod_expires.c>
    ExpiresActive On
    ExpiresByType text/css "access plus 1 month"
    ExpiresByType application/javascript "access plus 1 month"
    ExpiresByType image/png "access plus 1 year"
    ExpiresByType image/jpeg "access plus 1 year"
    ExpiresByType image/gif "access plus 1 year"
    ExpiresByType image/webp "access plus 1 year"
    ExpiresByType image/x-icon "access plus 1 year"
    ExpiresByType font/woff2 "access plus 1 year"
</IfModule>
```

### httpd-vhosts.conf - VirtualHosts

**IMPORTANTE:** Esta é a configuração central do proxy reverso.

#### VirtualHost HTTP (porta 80)
```apache
<VirtualHost *:80>
    ServerName localhost
    DocumentRoot "C:/xampp/phpMyAdmin"

    # Apenas acesso local ao phpMyAdmin
    <Directory "C:/xampp/phpMyAdmin">
        AllowOverride All
        Require local
    </Directory>
</VirtualHost>
```

**Nota:** Este VirtualHost serve apenas phpMyAdmin localmente. Não há redirecionamento HTTP→HTTPS.

#### VirtualHost HTTPS (porta 443) - PROXY REVERSO

```apache
<VirtualHost *:443>
    ServerName portal.jpcontabil.com.br

    # ========== SSL/TLS Configuration ==========
    SSLEngine on
    SSLCertificateFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-crt.pem"
    SSLCertificateKeyFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-key.pem"
    SSLCertificateChainFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-chain.pem"

    # ========== Proxy Configuration ==========
    # Preserva o hostname original da requisição
    ProxyPreserveHost On

    # Timeout de 300 segundos (5 minutos) para operações longas
    ProxyTimeout 300

    # Timeout de leitura de requisições
    # Headers: 30-60s com taxa mínima de 500 bytes/s
    # Body: 30-60s com taxa mínima de 500 bytes/s
    RequestReadTimeout header=30-60,MinRate=500 body=30-60,MinRate=500

    # Proxy para Flask/Waitress
    # ⚠️ ATENÇÃO: Configurado para porta 5000, mas aplicação roda em 9000
    # TODO: Alterar para http://127.0.0.1:9000/
    ProxyPass / http://127.0.0.1:5000/ retry=0 timeout=300 acquire=300 keepalive=On
    ProxyPassReverse / http://127.0.0.1:5000/

    # Parâmetros:
    # - retry=0: Não tenta novamente em caso de falha
    # - timeout=300: Timeout de 300s para backend
    # - acquire=300: Timeout para adquirir conexão do pool
    # - keepalive=On: Mantém conexões persistentes com backend
</VirtualHost>
```

**⚠️ CORREÇÃO NECESSÁRIA:**
A porta está configurada como **5000**, mas a aplicação Flask roda na porta **9000**.
Alterar linhas 51-52 para:
```apache
ProxyPass / http://127.0.0.1:9000/ retry=0 timeout=300 acquire=300 keepalive=On
ProxyPassReverse / http://127.0.0.1:9000/
```

### httpd-ssl.conf - Configuração SSL Global

```apache
# Porta HTTPS
Listen 443

# Cipher Suites (algoritmos de criptografia)
# HIGH: Criptografia forte (128+ bits)
# !MD5, !RC4, !3DES: Desabilita algoritmos fracos
SSLCipherSuite HIGH:MEDIUM:!MD5:!RC4:!3DES
SSLProxyCipherSuite HIGH:MEDIUM:!MD5:!RC4:!3DES

# Protocolo SSL/TLS
# all: Todos os protocolos
# -SSLv3: Desabilita SSLv3 (vulnerável ao POODLE)
SSLProtocol all -SSLv3
SSLProxyProtocol all -SSLv3

# Servidor escolhe o cipher (não o cliente)
SSLHonorCipherOrder on

# Cache de sessões SSL (para performance)
SSLSessionCache "shmcb:C:/xampp/apache/logs/ssl_scache(512000)"
SSLSessionCacheTimeout 300  # 5 minutos
```

**Cipher Suite em uso:**
- RSA 3072-bit (chave pública/privada)
- AES-256-GCM (criptografia simétrica)
- SHA-256 (hashing)

**Verificar cipher suites disponíveis:**
```batch
C:\xampp\apache\bin\openssl.exe ciphers -v 'HIGH:MEDIUM:!MD5:!RC4:!3DES'
```

### httpd-proxy.conf - Configuração de Proxy

```apache
<IfModule proxy_module>
<IfModule proxy_http_module>
    # Proxy Reverso (não Forward Proxy)
    ProxyRequests Off

    # Permite acesso a todos os backends
    <Proxy *>
        Require all granted
    </Proxy>
</IfModule>
</IfModule>
```

**ProxyRequests Off:**
- **Off:** Modo proxy reverso (nosso caso)
- **On:** Modo forward proxy (cliente usa servidor como proxy para acessar internet)

### httpd-default.conf - Timeouts

```apache
# Timeout geral para receber/enviar dados
Timeout 120

# KeepAlive: Permite múltiplas requisições na mesma conexão TCP
KeepAlive On

# Máximo de requisições por conexão KeepAlive
MaxKeepAliveRequests 100

# Timeout entre requisições na mesma conexão
KeepAliveTimeout 5

# Timeout de leitura de requisições
RequestReadTimeout header=20-40,MinRate=500 body=20,MinRate=500
```

**Diferença entre Timeout e ProxyTimeout:**
- **Timeout:** Conexão cliente ↔ Apache
- **ProxyTimeout:** Conexão Apache ↔ Flask

### httpd-mpm.conf - Multi-Processing Module

**Windows usa mpm_winnt (threads):**
```apache
<IfModule mpm_winnt_module>
    ThreadsPerChild 250           # Threads por processo
    MaxConnectionsPerChild 0      # 0 = infinito (não recicla processo)
</IfModule>
```

**Significado:**
- Apache cria 1 processo com 250 threads
- Cada thread atende 1 requisição simultânea
- Máximo de 250 conexões simultâneas

**Performance:**
- Mais threads = Mais concorrência
- Muito alto = Consumo excessivo de RAM
- Valor atual (250) é adequado para servidores pequenos/médios

### Verificar Configuração

**Testar sintaxe antes de aplicar:**
```batch
C:\xampp\apache\bin\httpd.exe -t
```

Saída esperada:
```
Syntax OK
```

**Testar e mostrar VirtualHosts:**
```batch
C:\xampp\apache\bin\httpd.exe -t -D DUMP_VHOSTS
```

**Aplicar alterações (reiniciar Apache):**
```batch
net stop Apache2.4
net start Apache2.4
```

Ou (reload gracioso):
```batch
C:\xampp\apache\bin\httpd.exe -k restart
```

---

## 8. Configurações Críticas do Flask

### Arquivo .env - Variáveis de Ambiente

**Localização:** `c:\Users\ti02\Desktop\site-teste\.env`

#### Configuração do Waitress

```env
# Porta do servidor Waitress
WAITRESS_PORT=9000

# Número de threads para processar requisições
WAITRESS_THREADS=32

# Timeout para canais (SSE - Server-Sent Events)
WAITRESS_CHANNEL_TIMEOUT=100

# Proxy confiável (Apache em localhost)
WAITRESS_TRUSTED_PROXY=127.0.0.1

# Número de proxies reversos na cadeia
WAITRESS_TRUSTED_PROXY_COUNT=1
```

**Explicação:**

| Parâmetro | Valor | Motivo |
|-----------|-------|--------|
| `WAITRESS_PORT` | 9000 | Porta alta (não requer root), match com ProxyPass |
| `WAITRESS_THREADS` | 32 | Alinhado com pool do Apache (max 64 / 2) |
| `WAITRESS_CHANNEL_TIMEOUT` | 100 | Suporta SSE (Server-Sent Events) de até 100s |
| `WAITRESS_TRUSTED_PROXY` | 127.0.0.1 | Apenas Apache local é confiável |
| `WAITRESS_TRUSTED_PROXY_COUNT` | 1 | Apenas 1 proxy na cadeia (Apache) |

#### Configuração de Segurança

```env
# Secret keys (manter em segredo!)
SECRET_KEY=REDACTED
CSRF_SECRET_KEY=REDACTED_CSRF
ENCRYPTION_KEY=53fr8VJIQK5rEklEDlZrx4vftzV0SsnjLhXtwxvYNxc=

# HTTPS enforcement
ENFORCE_HTTPS=false   # false = desenvolvimento, true = produção
```

**⚠️ IMPORTANTE:**
- **SECRET_KEY:** Usado para assinar sessions (cookies)
- **CSRF_SECRET_KEY:** Protege contra CSRF attacks
- **ENCRYPTION_KEY:** Criptografia de dados sensíveis
- Nunca compartilhar essas chaves em repositórios públicos
- Usar chaves diferentes para dev/staging/produção

**Gerar novas chaves:**
```python
# SECRET_KEY e CSRF_SECRET_KEY
import secrets
print(secrets.token_urlsafe(32))

# ENCRYPTION_KEY (Fernet)
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### run.py - Inicialização do Waitress

**Localização:** `c:\Users\ti02\Desktop\site-teste\run.py`

```python
import os
from waitress import serve
from app import create_app

# Criar aplicação Flask
app = create_app()

# Configuração de proxy reverso
def _get_int_env(key, default):
    """Helper para ler inteiros do .env"""
    return int(os.getenv(key, default))

# Configurações do Waitress
host = os.getenv("WAITRESS_HOST", "127.0.0.1")  # Localhost only
port = _get_int_env("WAITRESS_PORT", 9000)
threads = _get_int_env("WAITRESS_THREADS", 32)
channel_timeout = _get_int_env("WAITRESS_CHANNEL_TIMEOUT", 100)

# Trusted proxy configuration
trusted_proxy = os.getenv("WAITRESS_TRUSTED_PROXY", "127.0.0.1")
trusted_proxy_count = _get_int_env("WAITRESS_TRUSTED_PROXY_COUNT", 1)
trusted_proxy_headers = {"x-forwarded-proto"}  # Detecta HTTPS

if __name__ == '__main__':
    print(f"🚀 Starting Waitress WSGI server on {host}:{port}")
    print(f"📊 Threads: {threads}")
    print(f"🔒 Trusted proxy: {trusted_proxy}")

    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        channel_timeout=channel_timeout,

        # Proxy reverso
        trusted_proxy=trusted_proxy,
        trusted_proxy_count=trusted_proxy_count,
        trusted_proxy_headers=trusted_proxy_headers,
        clear_untrusted_proxy_headers=True,  # Limpa headers não confiáveis

        # Performance
        connection_limit=256,   # Máximo de conexões simultâneas
        backlog=256,            # Fila de conexões pendentes
        recv_bytes=32768,       # Buffer TCP de recebimento (32KB)
        send_bytes=32768,       # Buffer TCP de envio (32KB)
    )
```

**Parâmetros importantes:**

| Parâmetro | Valor | Explicação |
|-----------|-------|-----------|
| `host='127.0.0.1'` | localhost | **NÃO usar 0.0.0.0** (exporia aplicação) |
| `port=9000` | Porta alta | Match com ProxyPass do Apache |
| `threads=32` | 32 threads | Processa até 32 requisições simultâneas |
| `channel_timeout=100` | 100s | Timeout para SSE (notificações em tempo real) |
| `trusted_proxy='127.0.0.1'` | Apache local | Apenas Apache pode enviar headers X-Forwarded-* |
| `trusted_proxy_headers` | {'x-forwarded-proto'} | Detecta HTTPS via header |
| `clear_untrusted_proxy_headers=True` | Limpa headers | Remove headers X-Forwarded-* de fontes não confiáveis |

### app/__init__.py - Inicialização do Flask

**Localização:** `c:\Users\ti02\Desktop\site-teste\app\__init__.py`

```python
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

def create_app():
    app = Flask(__name__)

    # Carregar configuração do .env
    app.config.from_object('config.Config')

    # ProxyFix: Processa headers X-Forwarded-*
    # x_for=1: Confia em 1 proxy para X-Forwarded-For
    # x_proto=1: Confia em 1 proxy para X-Forwarded-Proto
    # x_host=1: Confia em 1 proxy para X-Forwarded-Host
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1
    )

    # ... (resto da inicialização)

    return app
```

**ProxyFix explicado:**

Sem ProxyFix:
```python
# Flask veria:
request.remote_addr = '127.0.0.1'  # IP do Apache (não do cliente)
request.is_secure = False  # HTTP (não HTTPS)
request.host = '127.0.0.1:9000'  # Host local (não original)
```

Com ProxyFix:
```python
# Flask processa headers X-Forwarded-* e corrige:
request.remote_addr = '200.150.100.50'  # IP real do cliente
request.is_secure = True  # HTTPS (via X-Forwarded-Proto)
request.host = 'portal.jpcontabil.com.br'  # Host original
```

**Segurança do ProxyFix:**
- Apenas headers de `127.0.0.1` são confiáveis (Waitress valida)
- Headers de outras fontes são ignorados
- Previne header injection attacks

### Verificar se Aplicação está Rodando

**Via netstat:**
```batch
netstat -ano | findstr :9000
```

Saída esperada:
```
TCP    127.0.0.1:9000    0.0.0.0:0    LISTENING    12345
```

**Via curl:**
```batch
curl http://localhost:9000/health
```

Saída esperada:
```json
{"status": "ok", "version": "v2.0.4"}
```

**Via Python:**
```python
import requests
response = requests.get('http://localhost:9000/health')
print(response.json())
```

---

## 9. Redirecionamento HTTP para HTTPS

### Situação Atual

**Atualmente NÃO há redirecionamento HTTP→HTTPS configurado.**

Isso significa:
- Usuário acessa `http://portal.jpcontabil.com.br` → **Erro (conexão recusada)**
- Usuário acessa `https://portal.jpcontabil.com.br` → **✅ Funciona**

**Por que não há VirtualHost HTTP para portal.jpcontabil.com.br?**
- O VirtualHost HTTP (*:80) atual serve apenas `localhost` (phpMyAdmin)
- Não há configuração para `portal.jpcontabil.com.br` na porta 80

### Como Implementar Redirecionamento HTTP→HTTPS

#### Opção 1: Redirect Simples (Recomendado)

Adicionar no `httpd-vhosts.conf`:

```apache
# VirtualHost HTTP para portal.jpcontabil.com.br
<VirtualHost *:80>
    ServerName portal.jpcontabil.com.br

    # Redirect permanente (301) para HTTPS
    RewriteEngine On
    RewriteCond %{HTTPS} off
    RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]

    # Logs
    ErrorLog "C:/xampp/apache/logs/portal-http-error.log"
    CustomLog "C:/xampp/apache/logs/portal-http-access.log" combined
</VirtualHost>
```

**Funcionamento:**
```
Cliente: http://portal.jpcontabil.com.br/tasks
  ↓
Apache VirtualHost *:80
  ↓
RewriteRule: 301 Redirect → https://portal.jpcontabil.com.br/tasks
  ↓
Cliente: https://portal.jpcontabil.com.br/tasks (nova requisição)
  ↓
Apache VirtualHost *:443 (proxy para Flask)
```

#### Opção 2: Redirect com WWW

Se quiser forçar uso de `www.`:

```apache
<VirtualHost *:80>
    ServerName portal.jpcontabil.com.br
    ServerAlias www.portal.jpcontabil.com.br

    RewriteEngine On

    # Redirect de portal.jpcontabil.com.br → www.portal.jpcontabil.com.br
    RewriteCond %{HTTP_HOST} ^portal\.jpcontabil\.com\.br$ [NC]
    RewriteRule ^(.*)$ https://www.portal.jpcontabil.com.br$1 [R=301,L]

    # Redirect de HTTP → HTTPS (para www.)
    RewriteCond %{HTTPS} off
    RewriteRule ^(.*)$ https://%{HTTP_HOST}$1 [R=301,L]
</VirtualHost>
```

#### Opção 3: Redirect com HSTS

**HSTS (HTTP Strict Transport Security):** Força navegador a sempre usar HTTPS.

```apache
<VirtualHost *:80>
    ServerName portal.jpcontabil.com.br

    # Redirect para HTTPS
    RewriteEngine On
    RewriteCond %{HTTPS} off
    RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]
</VirtualHost>

<VirtualHost *:443>
    ServerName portal.jpcontabil.com.br

    # SSL config...

    # HSTS: Força HTTPS por 1 ano (31536000 segundos)
    # includeSubDomains: Aplica a todos os subdomínios
    # preload: Permite inclusão na lista HSTS preload do browser
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"

    # Proxy config...
</VirtualHost>
```

**⚠️ CUIDADO com HSTS:**
- Uma vez ativado, navegador **nunca mais** tentará HTTP
- Se certificado expirar, site fica inacessível
- `includeSubDomains` afeta **TODOS** os subdomínios
- `preload` é permanente (difícil remover)

### Vantagens do Redirecionamento HTTP→HTTPS

| Vantagem | Explicação |
|----------|-----------|
| **Segurança** | Todo tráfego sempre criptografado |
| **Anti-MITM** | Previne ataques man-in-the-middle |
| **SEO** | Google prioriza sites HTTPS no ranking |
| **Confiança** | Navegadores mostram cadeado (usuário confia mais) |
| **Conformidade** | LGPD, PCI-DSS exigem HTTPS para dados sensíveis |
| **HTTP/2** | Apenas disponível via HTTPS |

### Desvantagens / Considerações

| Desvantagem | Mitigação |
|-------------|-----------|
| **APIs HTTP** | Testar integrações que usam HTTP (webhooks, callbacks) |
| **Certificado expirado** | Configurar renovação automática (Certbot) |
| **HSTS muito agressivo** | Não usar `preload` inicialmente |
| **Redirect loops** | Testar configuração antes de aplicar |

### Testar Redirecionamento

Após configurar, testar:

```batch
# Via curl (ver headers)
curl -I http://portal.jpcontabil.com.br

# Saída esperada:
HTTP/1.1 301 Moved Permanently
Location: https://portal.jpcontabil.com.br/
```

```batch
# Via browser
start http://portal.jpcontabil.com.br
# Deve redirecionar automaticamente para HTTPS
```

### Aplicar Configuração

```batch
# 1. Editar httpd-vhosts.conf
notepad C:\xampp\apache\conf\extra\httpd-vhosts.conf

# 2. Testar sintaxe
C:\xampp\apache\bin\httpd.exe -t

# 3. Reiniciar Apache
net stop Apache2.4
net start Apache2.4
```

---

## 10. Certificados SSL

### Informações do Certificado Atual

**Provedor:** Let's Encrypt (Autoridade Certificadora)
**Emissor:** R13 (Let's Encrypt Intermediate CA)
**Domínio:** portal.jpcontabil.com.br
**Algoritmo:** RSA 3072-bit com SHA-256

**Validade:**
- **Início:** 12 de Janeiro de 2026, 18:48:07 GMT
- **Término:** 12 de Abril de 2026, 18:48:06 GMT
- **Duração:** 90 dias (3 meses - padrão Let's Encrypt)

**Status:** ✅ Válido (expira em ~60 dias)

### Localização dos Arquivos

**Diretório:** `C:\Certificados\portaljp\`

```
C:\Certificados\portaljp\
├── portal.jpcontabil.com.br-crt.pem         (2.024 bytes)  ← Certificado público
├── portal.jpcontabil.com.br-key.pem         (2.498 bytes)  ← Chave privada
├── portal.jpcontabil.com.br-chain.pem       (3.854 bytes)  ← Chain completa
└── portal.jpcontabil.com.br-chain-only.pem  (1.830 bytes)  ← Chain sem cert
```

**Descrição dos arquivos:**

| Arquivo | Conteúdo | Uso |
|---------|----------|-----|
| **-crt.pem** | Certificado público do domínio | Apache apresenta ao cliente (SSLCertificateFile) |
| **-key.pem** | Chave privada RSA 3072-bit | Apache usa para descriptografar (SSLCertificateKeyFile) |
| **-chain.pem** | Chain de certificados (R13 + ISRG Root X1) | Valida cadeia de confiança (SSLCertificateChainFile) |
| **-chain-only.pem** | Apenas intermediários (sem cert) | Alternativa ao chain.pem |

### Estrutura do Certificado

```
Certificado portal.jpcontabil.com.br (End-Entity)
  ↓ Assinado por
Certificado Intermediário R13 (Let's Encrypt)
  ↓ Assinado por
Certificado Root ISRG Root X1 (Internet Security Research Group)
  ↓ Confiável em
Navegadores (Mozilla, Google, Apple, Microsoft)
```

**Cadeia de confiança:**
1. Cliente conecta em `https://portal.jpcontabil.com.br`
2. Apache apresenta certificado `portal.jpcontabil.com.br-crt.pem`
3. Navegador verifica:
   - ✅ Certificado assinado por R13?
   - ✅ R13 assinado por ISRG Root X1?
   - ✅ ISRG Root X1 está na lista de CAs confiáveis do navegador?
   - ✅ Certificado não expirado?
   - ✅ Domínio no certificado = domínio acessado?
4. Se tudo OK → 🔒 Cadeado verde

### Renovação de Certificados

**Frequência:** A cada 60-80 dias (antes de expirar aos 90 dias)

#### Método 1: Certbot (Recomendado)

**Instalar Certbot:**
```batch
# Via Chocolatey
choco install certbot

# Ou baixar: https://github.com/certbot/certbot/releases
```

**Renovar certificado (manual):**
```batch
certbot certonly --standalone -d portal.jpcontabil.com.br

# OU (se Apache estiver rodando)
certbot certonly --webroot -w C:\xampp\htdocs -d portal.jpcontabil.com.br
```

**Renovação automática:**
```batch
# Criar tarefa agendada (Task Scheduler)
schtasks /create /tn "Certbot Renewal" /tr "C:\Program Files\Certbot\certbot.exe renew" /sc daily /st 03:00
```

**Testar renovação (dry-run):**
```batch
certbot renew --dry-run
```

#### Método 2: win-acme (Windows-friendly)

**Download:** https://www.win-acme.com/

```batch
# Executar wacs.exe
wacs.exe --target manual --host portal.jpcontabil.com.br --installation iis

# win-acme cria tarefa agendada automaticamente
```

#### Método 3: Manual (win-acme ou acme.sh)

```batch
# acme.sh (Git Bash no Windows)
curl https://get.acme.sh | sh
acme.sh --issue -d portal.jpcontabil.com.br --standalone
```

### Verificar Certificado

**Via OpenSSL:**
```batch
# Ver detalhes do certificado
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -text -noout

# Ver data de expiração
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -noout -enddate
```

**Via browser:**
1. Acessar `https://portal.jpcontabil.com.br`
2. Clicar no cadeado 🔒
3. "Certificado" → Ver detalhes

**Via site externo:**
- https://www.ssllabs.com/ssltest/analyze.html?d=portal.jpcontabil.com.br
- Mostra grade de segurança (A+, A, B, etc.)

### Configuração no Apache

**httpd-vhosts.conf:**
```apache
<VirtualHost *:443>
    ServerName portal.jpcontabil.com.br

    SSLEngine on
    SSLCertificateFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-crt.pem"
    SSLCertificateKeyFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-key.pem"
    SSLCertificateChainFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-chain.pem"

    # ...
</VirtualHost>
```

**Recarregar após renovar certificado:**
```batch
# Graceful restart (não derruba conexões existentes)
C:\xampp\apache\bin\httpd.exe -k graceful
```

### Monitoramento de Expiração

**Script PowerShell para monitorar:**
```powershell
# check-ssl-expiry.ps1
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2("C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem")
$daysLeft = ($cert.NotAfter - (Get-Date)).Days
Write-Host "Certificado expira em $daysLeft dias ($($cert.NotAfter))"

if ($daysLeft -lt 30) {
    Write-Host "⚠️ ATENÇÃO: Certificado expira em menos de 30 dias!" -ForegroundColor Red
}
```

**Agendar execução:**
```batch
schtasks /create /tn "SSL Expiry Check" /tr "powershell.exe -File C:\scripts\check-ssl-expiry.ps1" /sc weekly /st 09:00
```

### Troubleshooting SSL

**Problema 1: Certificado não confiável**
```
NET::ERR_CERT_AUTHORITY_INVALID
```
- Chain file está faltando ou incorreto
- Adicionar `SSLCertificateChainFile`

**Problema 2: Certificado expirado**
```
NET::ERR_CERT_DATE_INVALID
```
- Renovar certificado urgentemente
- Verificar data/hora do servidor

**Problema 3: Nome incompatível**
```
NET::ERR_CERT_COMMON_NAME_INVALID
```
- Domínio acessado ≠ domínio no certificado
- Gerar certificado para domínio correto
- Adicionar SANs (Subject Alternative Names) se necessário

---

## 11. Segurança

### Implementações de Segurança Atuais

#### Camada 1: Apache

✅ **SSL/TLS:**
- TLS 1.2+ apenas (SSLv3 desabilitado - vulnerável ao POODLE)
- Cipher suites fortes (HIGH:MEDIUM, sem MD5/RC4/3DES)
- RSA 3072-bit (recomendado: 2048+ bits)
- Certificado válido Let's Encrypt

✅ **Timeouts:**
- RequestReadTimeout com MinRate (previne Slowloris attack)
- ProxyTimeout 300s (previne DoS)
- KeepAliveTimeout 5s (libera conexões ociosas)

✅ **Compressão:**
- GZIP habilitado (reduz banda)
- Não comprime imagens (evita BREACH attack potencial)

✅ **Logs:**
- access.log: Todas as requisições
- error.log: Erros e avisos
- Rotação automática (evita logs gigantes)

#### Camada 2: Flask/Waitress

✅ **Isolamento:**
- Bind em 127.0.0.1 (não 0.0.0.0)
- Porta 9000 (inacessível externamente)
- Trusted proxy validation (apenas 127.0.0.1)

✅ **CSRF Protection (Flask-WTF):**
- Tokens CSRF em todos os forms POST
- Validação automática de tokens
- SameSite cookies

✅ **Autenticação:**
- Flask-Login (sessions)
- Google OAuth 2.0 (login social)
- Senhas não armazenadas em plaintext (hashing)

✅ **Rate Limiting:**
- Flask-Limiter
- Limita requisições por IP
- Previne brute force attacks

✅ **Input Validation:**
- SQLAlchemy ORM (prepared statements)
- Jinja2 auto-escaping (previne XSS)
- Validação de uploads (extensões permitidas)

✅ **Cookies Seguros:**
```python
SESSION_COOKIE_SECURE = True       # Apenas HTTPS
SESSION_COOKIE_HTTPONLY = True     # Não acessível via JavaScript
SESSION_COOKIE_SAMESITE = 'Lax'    # Previne CSRF
```

### Recomendações Adicionais

#### ⚠️ 1. HSTS (HTTP Strict Transport Security)

**Status:** ❌ Não configurado

**Como implementar:**
```apache
<VirtualHost *:443>
    # ...
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"
</VirtualHost>
```

**Benefício:** Navegador sempre usa HTTPS (mesmo se usuário digitar http://)

#### ⚠️ 2. OCSP Stapling

**Status:** ❌ Não configurado (comentado no httpd-ssl.conf)

**Como implementar:**
```apache
# httpd-ssl.conf
SSLUseStapling On
SSLStaplingCache "shmcb:C:/xampp/apache/logs/ssl_stapling(128000)"
SSLStaplingResponderTimeout 5
SSLStaplingReturnResponderErrors off
```

**Benefício:** Validação de certificado mais rápida (não consulta OCSP server)

#### ⚠️ 3. Content Security Policy (CSP)

**Status:** ❌ Não configurado

**Como implementar:**
```apache
<VirtualHost *:443>
    # Previne XSS e injection attacks
    Header always set Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'"
</VirtualHost>
```

**Benefício:** Navegador bloqueia scripts/estilos de origens não autorizadas

#### ⚠️ 4. X-Frame-Options

**Status:** ⚠️ Verificar se configurado

**Como implementar:**
```apache
<VirtualHost *:443>
    # Previne clickjacking
    Header always set X-Frame-Options "DENY"
</VirtualHost>
```

**Benefício:** Impede que site seja carregado em iframe (previne clickjacking)

#### ⚠️ 5. Backup Regular

**Recomendação:**
- Backup diário do banco de dados MySQL
- Backup semanal de arquivos da aplicação
- Backup mensal de configurações do Apache
- Armazenar backups em local externo (nuvem ou servidor secundário)

**Script exemplo (PowerShell):**
```powershell
# backup-mysql.ps1
$date = Get-Date -Format "yyyy-MM-dd"
$backupFile = "C:\Backups\mysql\cadastro_empresas_teste_$date.sql"

& "C:\xampp\mysql\bin\mysqldump.exe" -u root -p"REDACTED" cadastro_empresas_teste > $backupFile

# Comprimir
Compress-Archive -Path $backupFile -DestinationPath "$backupFile.zip"
Remove-Item $backupFile

# Reter apenas últimos 30 dias
Get-ChildItem "C:\Backups\mysql\*.zip" | Where-Object {$_.LastWriteTime -lt (Get-Date).AddDays(-30)} | Remove-Item
```

#### ⚠️ 6. Firewall

**Recomendação:**
- Apenas portas 80 e 443 expostas publicamente
- Porta 3306 (MySQL) bloqueada externamente
- Porta 9000 (Flask) bloqueada externamente

**Verificar firewall Windows:**
```batch
netsh advfirewall firewall show rule name=all | findstr 80
netsh advfirewall firewall show rule name=all | findstr 443
```

#### ⚠️ 7. Secrets Management

**Status:** ⚠️ Secrets no .env (plaintext)

**Recomendação:**
- Usar Windows Credential Manager
- Ou variáveis de ambiente do sistema (não arquivo .env)
- Ou solução enterprise (HashiCorp Vault, Azure Key Vault)

**Migração para environment variables:**
```batch
# Configurar permanentemente
setx SECRET_KEY "REDACTED" /M
setx CSRF_SECRET_KEY "REDACTED_CSRF" /M

# run.py passa a ler de os.environ (não .env)
```

### Checklist de Segurança

| Item | Status | Prioridade |
|------|--------|-----------|
| ✅ HTTPS com certificado válido | ✅ OK | Alta |
| ✅ SSL/TLS configurado corretamente | ✅ OK | Alta |
| ✅ Aplicação isolada em localhost | ✅ OK | Alta |
| ✅ CSRF protection | ✅ OK | Alta |
| ✅ Autenticação implementada | ✅ OK | Alta |
| ✅ Input validation (SQL injection) | ✅ OK | Alta |
| ✅ XSS protection (auto-escaping) | ✅ OK | Alta |
| ⚠️ HSTS | ❌ Faltando | Média |
| ⚠️ OCSP Stapling | ❌ Faltando | Baixa |
| ⚠️ CSP | ❌ Faltando | Média |
| ⚠️ X-Frame-Options | ⚠️ Verificar | Média |
| ⚠️ Backup automático | ⚠️ Verificar | Alta |
| ⚠️ Firewall configurado | ⚠️ Verificar | Alta |
| ⚠️ Secrets management | ❌ Plaintext | Média |

---

## 12. Performance

### Otimizações Apache

#### 1. Compressão GZIP

**Status:** ✅ Habilitado

```apache
<IfModule mod_deflate.c>
    AddOutputFilterByType DEFLATE text/html text/plain text/xml
    AddOutputFilterByType DEFLATE text/css text/javascript application/javascript
    AddOutputFilterByType DEFLATE application/json application/xml
</IfModule>
```

**Impacto:**
- HTML: ~15KB → ~4KB (73% redução)
- CSS: ~50KB → ~10KB (80% redução)
- JSON: ~20KB → ~5KB (75% redução)

**Benefício:** Menos banda consumida, carregamento mais rápido

#### 2. Cache de Browser (mod_expires)

**Status:** ✅ Habilitado

```apache
<IfModule mod_expires.c>
    ExpiresActive On
    ExpiresByType text/css "access plus 1 month"
    ExpiresByType application/javascript "access plus 1 month"
    ExpiresByType image/png "access plus 1 year"
    ExpiresByType image/jpeg "access plus 1 year"
    ExpiresByType font/woff2 "access plus 1 year"
</IfModule>
```

**Impacto:**
- CSS/JS: Navegador cacheia por 30 dias
- Imagens: Navegador cacheia por 1 ano
- Fonts: Navegador cacheia por 1 ano

**Benefício:** Usuário não baixa mesmos arquivos repetidamente

#### 3. KeepAlive

**Status:** ✅ Habilitado

```apache
KeepAlive On
MaxKeepAliveRequests 100
KeepAliveTimeout 5
```

**Impacto:**
- Múltiplas requisições reutilizam mesma conexão TCP
- Economiza handshake TCP/SSL (200-300ms)

**Benefício:** Páginas com muitos recursos (CSS, JS, imagens) carregam mais rápido

#### 4. ThreadsPerChild

**Status:** ✅ 250 threads

```apache
<IfModule mpm_winnt_module>
    ThreadsPerChild 250
</IfModule>
```

**Impacto:**
- Até 250 requisições simultâneas
- Adequado para servidores pequenos/médios

**Monitorar:** Se Apache ficar lento, aumentar threads (300-500)

### Otimizações Waitress

#### 1. Threads

**Status:** ✅ 32 threads

```python
threads=32
```

**Impacto:**
- 32 requisições Flask simultâneas
- Alinhado com pool do Apache (250/2 ≈ 125, conservador em 32)

**Benefício:** Boa concorrência sem sobrecarregar CPU

#### 2. Connection Limit

**Status:** ✅ 256 conexões

```python
connection_limit=256
backlog=256
```

**Impacto:**
- Até 256 conexões simultâneas aceitas
- 256 conexões em fila (aguardando thread disponível)

**Benefício:** Não rejeita conexões sob carga moderada

#### 3. Buffers TCP

**Status:** ✅ 32KB

```python
recv_bytes=32768  # 32KB
send_bytes=32768  # 32KB
```

**Impacto:**
- Buffers maiores = menos syscalls
- Melhor throughput para respostas grandes

**Benefício:** Performance melhorada para uploads/downloads

### Otimizações Flask

#### 1. SQLAlchemy Connection Pool

**Recomendação:** Configurar pool de conexões

```python
# config.py
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_size': 10,           # 10 conexões persistentes
    'pool_recycle': 3600,      # Recicla conexões a cada 1h
    'pool_pre_ping': True,     # Testa conexão antes de usar
    'max_overflow': 5,         # Até 15 conexões (10 + 5 overflow)
}
```

**Benefício:** Não abre/fecha conexão MySQL a cada requisição

#### 2. Flask-Caching

**Status:** ⚠️ Verificar se configurado

```python
from flask_caching import Cache

cache = Cache(app, config={
    'CACHE_TYPE': 'SimpleCache',  # Ou 'redis' para produção
    'CACHE_DEFAULT_TIMEOUT': 300  # 5 minutos
})

@app.route('/api/stats')
@cache.cached(timeout=60)
def get_stats():
    # Consulta pesada no banco
    return jsonify(stats)
```

**Benefício:** Consultas pesadas não executam a cada requisição

#### 3. Lazy Loading

**Status:** ⚠️ Verificar implementação

```python
# Carregar relacionamentos apenas quando necessário
class Task(db.Model):
    # ...
    comments = db.relationship('Comment', lazy='dynamic')  # Não carrega automaticamente
```

**Benefício:** Reduz queries desnecessárias

### Monitoramento de Performance

#### Apache

**Habilitar mod_status:**
```apache
<Location "/server-status">
    SetHandler server-status
    Require local
</Location>
```

Acessar: `http://localhost/server-status`

**Métricas:**
- Requests per second
- Bytes per second
- Threads ocupados vs idle

#### Waitress/Flask

**Logs de performance:**
```python
import logging
logging.basicConfig(level=logging.INFO)

# Ver tempo de resposta de cada rota
```

**Ferramentas:**
- Flask-DebugToolbar (apenas dev)
- New Relic APM (produção)
- Datadog (produção)

#### MySQL

**Slow query log:**
```ini
# my.ini
slow_query_log = 1
slow_query_log_file = C:/xampp/mysql/data/slow.log
long_query_time = 2  # Queries > 2s são logadas
```

**Analisar:**
```batch
C:\xampp\mysql\bin\mysqldumpslow.exe C:\xampp\mysql\data\slow.log
```

### Benchmarking

**Apache Bench:**
```batch
# 1000 requisições, 10 simultâneas
C:\xampp\apache\bin\ab.exe -n 1000 -c 10 https://portal.jpcontabil.com.br/

# Com KeepAlive
C:\xampp\apache\bin\ab.exe -n 1000 -c 10 -k https://portal.jpcontabil.com.br/
```

**Métricas importantes:**
- Requests per second (RPS)
- Time per request (latência)
- Failed requests (erros)

---

## 13. Troubleshooting

### Apache Não Inicia

#### Problema 1: Porta 80/443 em Uso

**Sintoma:**
```
(OS 10048)Only one usage of each socket address (protocol/network address/port) is normally permitted.
```

**Diagnóstico:**
```batch
# Ver processo usando porta 80
netstat -ano | findstr :80

# Saída exemplo:
TCP    0.0.0.0:80    0.0.0.0:0    LISTENING    1234
```

**Solução 1 - Matar processo:**
```batch
taskkill /PID 1234 /F
```

**Solução 2 - Identificar aplicação:**
```batch
# Ver qual programa é o PID 1234
tasklist /FI "PID eq 1234"

# Comum culpados:
# - Skype (usar porta alternativa em Skype settings)
# - IIS (parar: net stop was /y)
# - Outro Apache (verificar instalações duplicadas)
# - SQL Server Reporting Services (parar serviço)
```

**Solução 3 - Usar porta alternativa (temporário):**
```apache
# httpd.conf
Listen 8080  # Em vez de 80

<VirtualHost *:8080>
    # ...
</VirtualHost>
```

#### Problema 2: Configuração Inválida

**Sintoma:**
```
AH00526: Syntax error on line 42 of C:/xampp/apache/conf/extra/httpd-vhosts.conf:
Invalid command 'ProxyPass', perhaps misspelled...
```

**Diagnóstico:**
```batch
C:\xampp\apache\bin\httpd.exe -t
```

**Solução - Módulo faltando:**
```apache
# httpd.conf - Verificar se está descomentado:
LoadModule proxy_module modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
```

**Solução - Erro de sintaxe:**
```apache
# Verificar aspas, parênteses, tags fechadas
<VirtualHost *:443>
    # ...
</VirtualHost>  ← Não esquecer de fechar
```

#### Problema 3: DLLs Faltando

**Sintoma:**
```
The program can't start because VCRUNTIME140.dll is missing
```

**Solução:**
- Baixar Visual C++ Redistributable 2015-2022
- Link: https://aka.ms/vs/17/release/vc_redist.x64.exe
- Instalar e reiniciar

#### Problema 4: Permissões

**Sintoma:**
```
(OS 5)Access is denied. : AH00072: make_sock: could not bind to address 0.0.0.0:80
```

**Solução:**
- Executar CMD como **Administrador**
- Ou adicionar permissão para usuário atual:
  ```batch
  netsh http add urlacl url=http://+:80/ user=EVERYONE
  ```

### Aplicação Flask Não Responde

#### Problema 1: Flask Não Rodando

**Sintoma:**
```
(OS 10061)No connection could be made because the target machine actively refused it.
```

**Diagnóstico:**
```batch
netstat -ano | findstr :9000
# Se nada aparecer → Flask não está rodando
```

**Solução:**
```batch
# Iniciar Flask
cd c:\Users\ti02\Desktop\site-teste
python run.py
```

**Verificar logs:**
```batch
# Ver últimas linhas do log
type c:\Users\ti02\Desktop\site-teste\logs\app.log
```

#### Problema 2: Porta Errada no ProxyPass

**Sintoma:** Apache inicia, mas retorna erro 502 Bad Gateway

**Diagnóstico:**
```apache
# httpd-vhosts.conf linha 51
ProxyPass / http://127.0.0.1:5000/   ← Errado (Flask roda em 9000)
```

**Solução:**
```apache
ProxyPass / http://127.0.0.1:9000/   ← Correto
ProxyPassReverse / http://127.0.0.1:9000/
```

Reiniciar Apache:
```batch
C:\xampp\apache\bin\httpd.exe -k restart
```

#### Problema 3: Firewall Bloqueando

**Sintoma:** Apache e Flask rodando, mas erro 502

**Diagnóstico:**
```batch
# Testar conexão localhost diretamente
curl http://localhost:9000/health

# Se falhar → Firewall bloqueando
```

**Solução:**
```batch
# Adicionar regra de firewall
netsh advfirewall firewall add rule name="Flask App" dir=in action=allow protocol=TCP localport=9000

# Ou desabilitar firewall temporariamente para testar
netsh advfirewall set allprofiles state off
```

### Erro 502 Bad Gateway

**Causa:** Apache não consegue conectar ao backend Flask

**Checklist:**
1. ✅ Flask está rodando? (`netstat -ano | findstr :9000`)
2. ✅ Porta correta no ProxyPass? (9000 não 5000)
3. ✅ Firewall permite localhost:9000?
4. ✅ Waitress bind em 127.0.0.1? (não 0.0.0.0)

**Ver logs:**
```batch
# Apache error log
type C:\xampp\apache\logs\error.log | findstr "proxy:error"

# Saída exemplo:
[proxy:error] (OS 10061)No connection could be made because the target machine actively refused it.
```

### Erro 504 Gateway Timeout

**Causa:** Flask está demorando mais que ProxyTimeout para responder

**Diagnóstico:**
```apache
# httpd-vhosts.conf
ProxyTimeout 300   # 300 segundos (5 minutos)
```

**Solução 1 - Aumentar timeout:**
```apache
ProxyTimeout 600   # 10 minutos
```

**Solução 2 - Otimizar query lenta:**
```python
# Identificar query lenta no Flask
import time

@app.before_request
def before_request():
    g.start_time = time.time()

@app.after_request
def after_request(response):
    diff = time.time() - g.start_time
    if diff > 2:  # Requisições > 2s
        app.logger.warning(f'Slow request: {request.path} took {diff:.2f}s')
    return response
```

**Solução 3 - Adicionar índices no MySQL:**
```sql
-- Verificar queries sem índice
SELECT * FROM information_schema.PROCESSLIST WHERE Command != 'Sleep';

-- Adicionar índice (exemplo)
CREATE INDEX idx_user_id ON tasks(user_id);
```

### SSL Certificate Errors

#### NET::ERR_CERT_AUTHORITY_INVALID

**Causa:** Chain file faltando ou incorreto

**Solução:**
```apache
<VirtualHost *:443>
    # ...
    SSLCertificateChainFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-chain.pem"
</VirtualHost>
```

Reiniciar Apache:
```batch
C:\xampp\apache\bin\httpd.exe -k restart
```

#### NET::ERR_CERT_DATE_INVALID

**Causa:** Certificado expirado

**Diagnóstico:**
```batch
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -noout -enddate

# Saída: notAfter=Apr 12 18:48:06 2026 GMT
```

**Solução:**
Renovar certificado (ver [seção 10](#10-certificados-ssl))

### Logs Úteis

**Apache Error Log:**
```batch
type C:\xampp\apache\logs\error.log
```

**Apache Access Log:**
```batch
type C:\xampp\apache\logs\access.log
```

**Flask Logs:**
```batch
type c:\Users\ti02\Desktop\site-teste\logs\app.log
```

**XAMPP Control Panel Log:**
```batch
type C:\xampp\xampp-control.log
```

**MySQL Error Log:**
```batch
type C:\xampp\mysql\data\mysql_error.log
```

### Comandos de Diagnóstico Rápido

**Status geral do sistema:**
```batch
# Apache rodando?
sc query Apache2.4

# Portas em uso
netstat -ano | findstr ":80 :443 :9000 :3306"

# Processos Apache e Python
tasklist | findstr "httpd.exe python.exe"

# Testar configuração Apache
C:\xampp\apache\bin\httpd.exe -t

# Testar Flask
curl http://localhost:9000/health

# Ver últimos erros Apache
powershell "Get-Content C:\xampp\apache\logs\error.log -Tail 20"
```

---

## Conclusão

Este documento fornece uma visão completa da arquitetura de proxy reverso Apache + Flask implementada no Portal JP Contábil.

**Pontos-chave:**
- ✅ Apache gerencia SSL/TLS, proxy reverso, e arquivos estáticos
- ✅ Flask foca em lógica de negócio, isolado em localhost
- ✅ Segurança em múltiplas camadas (defesa em profundidade)
- ✅ Performance otimizada com compressão, cache e KeepAlive
- ⚠️ Correção necessária: ProxyPass porta 5000 → 9000
- ⚠️ Implementações recomendadas: HSTS, OCSP Stapling, CSP

**Próximos passos:**
1. Corrigir porta do ProxyPass (5000 → 9000)
2. Implementar redirecionamento HTTP→HTTPS (opcional)
3. Adicionar HSTS e headers de segurança
4. Configurar renovação automática de certificados
5. Implementar backup automático do banco de dados

**Suporte:**
- Documentação Apache: https://httpd.apache.org/docs/2.4/
- Documentação Waitress: https://docs.pylonsproject.org/projects/waitress/
- Documentação Flask: https://flask.palletsprojects.com/
- Let's Encrypt: https://letsencrypt.org/docs/

---

**Última atualização:** Fevereiro 2026
**Mantido por:** Equipe TI - JP Contábil
**Versão da aplicação:** v2.0.4
