# Guia de Referência Rápida - Apache + Flask

Comandos e configurações essenciais para consulta rápida.

---

## Gerenciamento Rápido

### Apache

```batch
# Iniciar
net start Apache2.4

# Parar
net stop Apache2.4

# Reiniciar (graceful - sem derrubar conexões)
C:\xampp\apache\bin\httpd.exe -k graceful

# Reiniciar (hard - derruba conexões)
net stop Apache2.4 && net start Apache2.4

# Testar configuração
C:\xampp\apache\bin\httpd.exe -t

# Ver VirtualHosts
C:\xampp\apache\bin\httpd.exe -t -D DUMP_VHOSTS

# Ver status
sc query Apache2.4
```

### Flask

```batch
# Iniciar aplicação
cd c:\Users\ti02\Desktop\site-teste
python run.py

# Verificar se está rodando
netstat -ano | findstr :9000

# Testar health check
curl http://localhost:9000/health
```

---

## Arquivos Importantes

### Configuração Apache

| Arquivo | Propósito |
|---------|-----------|
| `C:\xampp\apache\conf\httpd.conf` | Configuração principal |
| `C:\xampp\apache\conf\extra\httpd-vhosts.conf` | VirtualHosts (PROXY REVERSO) |
| `C:\xampp\apache\conf\extra\httpd-ssl.conf` | Configurações SSL globais |

### Certificados SSL

| Arquivo | Tipo |
|---------|------|
| `C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem` | Certificado público |
| `C:\Certificados\portaljp\portal.jpcontabil.com.br-key.pem` | Chave privada |
| `C:\Certificados\portaljp\portal.jpcontabil.com.br-chain.pem` | Chain completa |

### Aplicação Flask

| Arquivo | Propósito |
|---------|-----------|
| `c:\Users\ti02\Desktop\site-teste\.env` | Variáveis de ambiente |
| `c:\Users\ti02\Desktop\site-teste\run.py` | Entrada da aplicação |

---

## Logs

```batch
# Error log (últimas 20 linhas)
powershell "Get-Content C:\xampp\apache\logs\error.log -Tail 20"

# Access log (últimas 20 linhas)
powershell "Get-Content C:\xampp\apache\logs\access.log -Tail 20"

# Monitorar error log em tempo real
powershell "Get-Content C:\xampp\apache\logs\error.log -Wait -Tail 50"

# Procurar erros de proxy
findstr "[proxy:error]" C:\xampp\apache\logs\error.log
```

---

## Certificado SSL

```batch
# Ver data de expiração
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -noout -enddate

# Renovar (Certbot)
net stop Apache2.4
certbot renew --standalone
net start Apache2.4
```

---

## Configuração do Proxy Reverso

**Arquivo:** `C:\xampp\apache\conf\extra\httpd-vhosts.conf`

```apache
<VirtualHost *:443>
    ServerName portal.jpcontabil.com.br

    # SSL
    SSLEngine on
    SSLCertificateFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-crt.pem"
    SSLCertificateKeyFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-key.pem"
    SSLCertificateChainFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-chain.pem"

    # Proxy para Flask (porta 9000)
    ProxyPreserveHost On
    ProxyTimeout 300
    ProxyPass / http://127.0.0.1:9000/ retry=0 timeout=300 keepalive=On
    ProxyPassReverse / http://127.0.0.1:9000/
</VirtualHost>
```

⚠️ **Correção necessária:** Linha 51-52 está com porta 5000, alterar para 9000.

---

## Troubleshooting

### Apache não inicia

```batch
# Ver erro específico
C:\xampp\apache\bin\httpd.exe -t

# Ver última linha do error log
powershell "Get-Content C:\xampp\apache\logs\error.log -Tail 1"

# Ver processos usando porta 80/443
netstat -ano | findstr ":80 :443"
```

### Erro 502 Bad Gateway

**Causa:** Apache não consegue conectar ao Flask

**Checklist:**
1. Flask está rodando? `netstat -ano | findstr :9000`
2. Porta correta no ProxyPass? (9000 não 5000)
3. Ver error log: `type C:\xampp\apache\logs\error.log | findstr proxy:error`

### Erro 504 Gateway Timeout

**Causa:** Flask demorando muito para responder

**Solução:**
1. Aumentar ProxyTimeout no httpd-vhosts.conf
2. Verificar queries lentas no MySQL
3. Ver logs da aplicação Flask

---

## Portas

| Serviço | Porta | Acesso |
|---------|-------|--------|
| Apache HTTP | 80 | Público |
| Apache HTTPS | 443 | Público |
| Flask/Waitress | 9000 | Localhost apenas |
| MySQL | 3306 | Localhost apenas |

---

## Fluxo da Requisição

```
Cliente (Browser)
  ↓ HTTPS (porta 443)
Apache (Proxy Reverso)
  ↓ HTTP (porta 9000, localhost)
Waitress WSGI
  ↓
Flask Application
  ↓ SQL Queries
MySQL Database
```

---

## Módulos Apache Essenciais

```apache
LoadModule proxy_module modules/mod_proxy.so          # Proxy base
LoadModule proxy_http_module modules/mod_proxy_http.so # Proxy HTTP
LoadModule ssl_module modules/mod_ssl.so               # SSL/TLS
LoadModule headers_module modules/mod_headers.so       # Headers
LoadModule deflate_module modules/mod_deflate.so       # Compressão GZIP
LoadModule expires_module modules/mod_expires.so       # Cache browser
LoadModule rewrite_module modules/mod_rewrite.so       # URL rewrite
```

**Verificar módulos carregados:**
```batch
C:\xampp\apache\bin\httpd.exe -M
```

---

## Variáveis de Ambiente (.env)

```env
# Flask
WAITRESS_PORT=9000
WAITRESS_THREADS=32
WAITRESS_TRUSTED_PROXY=127.0.0.1

# Segurança
SECRET_KEY=TI02JPCONTABIL
CSRF_SECRET_KEY=TI02JPCONTABIL_CSRF
ENFORCE_HTTPS=false

# Database
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=jp098*
DB_NAME=cadastro_empresas_teste
```

---

## Backup Rápido

```batch
# Backup configuração Apache
xcopy C:\xampp\apache\conf C:\backup\apache\conf\ /E /I

# Backup certificados
xcopy C:\Certificados C:\backup\certificados\ /E /I
```

---

## Performance

### Apache

```apache
# Compressão GZIP
LoadModule deflate_module modules/mod_deflate.so

# Cache de browser
ExpiresActive On
ExpiresByType text/css "access plus 1 month"
ExpiresByType image/png "access plus 1 year"

# KeepAlive
KeepAlive On
MaxKeepAliveRequests 100
KeepAliveTimeout 5

# Threads
ThreadsPerChild 250
```

### Waitress

```python
threads=32                 # Concorrência
connection_limit=256       # Max conexões
channel_timeout=100        # Timeout SSE
recv_bytes=32768          # Buffer TCP
```

---

## Segurança

### Headers Recomendados

```apache
<VirtualHost *:443>
    # HSTS
    Header always set Strict-Transport-Security "max-age=31536000"

    # Clickjacking
    Header always set X-Frame-Options "DENY"

    # XSS Protection
    Header always set X-Content-Type-Options "nosniff"

    # CSP (Content Security Policy)
    Header always set Content-Security-Policy "default-src 'self'"
</VirtualHost>
```

### Isolamento

- ✅ Flask bind em **127.0.0.1** (não 0.0.0.0)
- ✅ MySQL bind em **localhost**
- ✅ Porta 9000 bloqueada externamente
- ✅ Apenas Apache exposto (portas 80/443)

---

## Comandos Úteis

```batch
# Ver conexões Apache ativas
netstat -ano | findstr "ESTABLISHED" | findstr "httpd"

# Contar requisições por status code
findstr " 200 " C:\xampp\apache\logs\access.log | find /c /v ""
findstr " 404 " C:\xampp\apache\logs\access.log | find /c /v ""
findstr " 500 " C:\xampp\apache\logs\access.log | find /c /v ""

# Ver tamanho dos logs
dir C:\xampp\apache\logs\*.log

# Verificar certificado SSL online
start https://www.ssllabs.com/ssltest/analyze.html?d=portal.jpcontabil.com.br
```

---

## Scripts PowerShell Rápidos

### Status Completo

```powershell
# Status Apache
$apacheService = Get-Service Apache2.4
Write-Host "Apache: $($apacheService.Status)"

# Status Flask
$flaskPort = netstat -ano | findstr ":9000"
if ($flaskPort) { Write-Host "Flask: RUNNING" } else { Write-Host "Flask: NOT RUNNING" }

# SSL expiration
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2("C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem")
$daysLeft = ($cert.NotAfter - (Get-Date)).Days
Write-Host "SSL expira em: $daysLeft dias"

# Tamanho dos logs
$errorLog = (Get-Item "C:\xampp\apache\logs\error.log").Length / 1MB
$accessLog = (Get-Item "C:\xampp\apache\logs\access.log").Length / 1MB
Write-Host "error.log: $([math]::Round($errorLog, 2)) MB"
Write-Host "access.log: $([math]::Round($accessLog, 2)) MB"
```

### Top 10 IPs

```powershell
Get-Content C:\xampp\apache\logs\access.log |
    ForEach-Object { ($_ -split " ")[0] } |
    Group-Object |
    Sort-Object Count -Descending |
    Select-Object -First 10 Count, Name
```

---

## Redirecionamento HTTP → HTTPS

**Não configurado atualmente.**

Para adicionar:

```apache
<VirtualHost *:80>
    ServerName portal.jpcontabil.com.br

    RewriteEngine On
    RewriteCond %{HTTPS} off
    RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]
</VirtualHost>
```

---

## Contatos

- **Equipe TI:** ti02@jpcontabil.com.br
- **Documentação completa:** [PROXY_REVERSO.md](PROXY_REVERSO.md)
- **Guia de manutenção:** [MANUTENCAO_APACHE.md](MANUTENCAO_APACHE.md)

---

**Última atualização:** Fevereiro 2026
