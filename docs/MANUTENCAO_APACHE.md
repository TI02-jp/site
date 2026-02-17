# Guia de Manutenção do Apache

**Portal JP Contábil**
**Versão:** 2.0.4

---

## Índice

1. [Comandos Rápidos](#comandos-rápidos)
2. [Gerenciamento do Serviço](#gerenciamento-do-serviço)
3. [Atualização de Configurações](#atualização-de-configurações)
4. [Renovação de Certificados SSL](#renovação-de-certificados-ssl)
5. [Adicionar Novos VirtualHosts](#adicionar-novos-virtualhosts)
6. [Backup de Configurações](#backup-de-configurações)
7. [Leitura e Análise de Logs](#leitura-e-análise-de-logs)
8. [Monitoramento](#monitoramento)
9. [Troubleshooting Comum](#troubleshooting-comum)

---

## Comandos Rápidos

### Gerenciamento do Serviço

```batch
# Iniciar Apache
net start Apache2.4

# Parar Apache
net stop Apache2.4

# Reiniciar Apache (método 1 - via serviço)
net stop Apache2.4 && net start Apache2.4

# Reiniciar Apache (método 2 - graceful, não derruba conexões)
C:\xampp\apache\bin\httpd.exe -k restart

# Graceful restart (recarrega config sem derrubar conexões)
C:\xampp\apache\bin\httpd.exe -k graceful

# Verificar status
sc query Apache2.4
```

### Verificação de Configuração

```batch
# Testar sintaxe da configuração
C:\xampp\apache\bin\httpd.exe -t

# Listar VirtualHosts configurados
C:\xampp\apache\bin\httpd.exe -t -D DUMP_VHOSTS

# Listar módulos carregados
C:\xampp\apache\bin\httpd.exe -M

# Ver versão do Apache
C:\xampp\apache\bin\httpd.exe -v
```

### Verificação de Portas

```batch
# Ver processos usando portas 80 e 443
netstat -ano | findstr ":80 :443"

# Ver apenas Apache
netstat -ano | findstr "httpd"

# Ver todas as conexões ativas do Apache
netstat -ano | findstr "ESTABLISHED" | findstr ":80 :443"
```

### Logs em Tempo Real

```batch
# PowerShell - Ver error log em tempo real
Get-Content C:\xampp\apache\logs\error.log -Wait -Tail 50

# PowerShell - Ver access log em tempo real
Get-Content C:\xampp\apache\logs\access.log -Wait -Tail 50

# CMD - Ver últimas 20 linhas do error log
powershell "Get-Content C:\xampp\apache\logs\error.log -Tail 20"
```

---

## Gerenciamento do Serviço

### Via Linha de Comando (Recomendado)

**Sempre executar CMD como Administrador:**
```batch
# Clicar direito em CMD → "Executar como administrador"
```

#### Instalar Serviço

```batch
cd C:\xampp\apache\bin
httpd.exe -k install -n "Apache2.4"
```

**Parâmetros:**
- `-k install`: Instala como serviço
- `-n "Apache2.4"`: Nome do serviço

**Resultado esperado:**
```
Installing the 'Apache2.4' service
The 'Apache2.4' service is successfully installed.
Testing httpd.conf....
Errors reported here must be corrected before the service can be started.
```

#### Desinstalar Serviço

```batch
# Parar serviço primeiro
net stop Apache2.4

# Desinstalar
cd C:\xampp\apache\bin
httpd.exe -k uninstall -n "Apache2.4"
```

#### Iniciar/Parar Serviço

```batch
# Iniciar
net start Apache2.4

# Parar
net stop Apache2.4

# Reiniciar (hard restart)
net stop Apache2.4 && net start Apache2.4
```

#### Reiniciar Gracefully (Sem Derrubar Conexões)

```batch
# Recarrega configuração sem interromper conexões ativas
C:\xampp\apache\bin\httpd.exe -k graceful
```

**Quando usar:**
- Após alterar configurações (httpd.conf, httpd-vhosts.conf)
- Após renovar certificados SSL
- Para aplicar mudanças sem downtime

**Diferença entre restart e graceful:**
| Comando | Conexões Ativas | Downtime | Quando Usar |
|---------|----------------|----------|-------------|
| `-k restart` | ❌ Derruba | ~2-5s | Mudanças críticas |
| `-k graceful` | ✅ Preserva | 0s | Mudanças de config |

### Via Painel XAMPP

```batch
# Abrir painel
C:\xampp\xampp-control.exe
```

**Interface:**
1. **Start:** Inicia Apache
2. **Stop:** Para Apache
3. **Config:**
   - Apache (httpd.conf)
   - Apache (httpd-ssl.conf)
   - PHP (php.ini)
4. **Logs:**
   - Apache (error.log)
   - Apache (access.log)
5. **Netstat:** Mostra portas em uso

**Configurar Autostart:**
- Marcar checkbox **Autostart** na linha Apache
- Apache iniciará automaticamente ao abrir o painel

### Via Gerenciador de Serviços Windows

```batch
# Abrir gerenciador de serviços
services.msc
```

**Operações:**
1. Localizar **Apache2.4** na lista
2. Clicar direito → **Iniciar/Parar/Reiniciar**
3. Clicar direito → **Propriedades**:
   - **Tipo de inicialização:**
     - **Automática:** Inicia com Windows
     - **Manual:** Requer inicialização manual
     - **Desabilitada:** Não pode iniciar
   - **Conta de logon:** LocalSystem (padrão)
   - **Dependências:** Tcpip, Afd

---

## Atualização de Configurações

### Workflow de Atualização Segura

1. **Fazer backup da configuração atual**
2. **Editar arquivos de configuração**
3. **Testar sintaxe**
4. **Aplicar mudanças (graceful restart)**
5. **Verificar logs**

### Passo 1: Backup

```batch
# Backup manual com timestamp
set timestamp=%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set timestamp=%timestamp: =0%

mkdir C:\backups\apache\%timestamp%
xcopy C:\xampp\apache\conf C:\backups\apache\%timestamp%\conf\ /E /I
```

**Ou usar script PowerShell:**
```powershell
# backup-apache-config.ps1
$date = Get-Date -Format "yyyy-MM-dd_HHmmss"
$backupPath = "C:\backups\apache\$date"

Copy-Item -Path "C:\xampp\apache\conf" -Destination $backupPath -Recurse
Write-Host "Backup criado em: $backupPath"
```

### Passo 2: Editar Configuração

**Principais arquivos:**
```
C:\xampp\apache\conf\httpd.conf                ← Configuração principal
C:\xampp\apache\conf\extra\httpd-vhosts.conf  ← VirtualHosts
C:\xampp\apache\conf\extra\httpd-ssl.conf     ← SSL global
C:\xampp\apache\conf\extra\httpd-proxy.conf   ← Proxy
```

**Editor recomendado:**
- Notepad++ (syntax highlighting)
- VS Code
- Sublime Text

**Abrir no Notepad:**
```batch
notepad C:\xampp\apache\conf\extra\httpd-vhosts.conf
```

### Passo 3: Testar Sintaxe

**SEMPRE testar antes de reiniciar:**
```batch
C:\xampp\apache\bin\httpd.exe -t
```

**Saída esperada (sucesso):**
```
Syntax OK
```

**Saída de erro (exemplo):**
```
AH00526: Syntax error on line 42 of C:/xampp/apache/conf/extra/httpd-vhosts.conf:
Invalid command 'ProxyPass', perhaps misspelled or defined by a module not included in the server configuration
```

**Corrigir erros antes de prosseguir!**

### Passo 4: Aplicar Mudanças

```batch
# Graceful restart (recomendado)
C:\xampp\apache\bin\httpd.exe -k graceful

# Ou via serviço (hard restart)
net stop Apache2.4 && net start Apache2.4
```

### Passo 5: Verificar Logs

```batch
# Ver últimas 20 linhas do error log
powershell "Get-Content C:\xampp\apache\logs\error.log -Tail 20"

# Procurar erros específicos
findstr /C:"error" /C:"warn" C:\xampp\apache\logs\error.log
```

**Verificar se Apache iniciou:**
```batch
sc query Apache2.4

# Saída esperada:
STATE              : 4  RUNNING
```

---

## Renovação de Certificados SSL

### Método 1: Certbot (Recomendado)

#### Instalação do Certbot

```batch
# Via Chocolatey
choco install certbot

# Ou download manual: https://github.com/certbot/certbot/releases
```

#### Renovar Certificado

```batch
# Parar Apache (Certbot precisa da porta 80)
net stop Apache2.4

# Renovar certificado (standalone mode)
certbot renew --standalone

# Ou especificar domínio
certbot certonly --standalone -d portal.jpcontabil.com.br

# Iniciar Apache novamente
net start Apache2.4
```

#### Renovação com Apache Rodando (Webroot)

```batch
# Apache não precisa parar
certbot renew --webroot -w C:\xampp\htdocs
```

#### Copiar Certificados para Local Correto

**Certbot salva em:** `C:\Certbot\live\portal.jpcontabil.com.br\`

```batch
# Copiar para C:\Certificados\portaljp\
copy C:\Certbot\live\portal.jpcontabil.com.br\fullchain.pem C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem
copy C:\Certbot\live\portal.jpcontabil.com.br\privkey.pem C:\Certificados\portaljp\portal.jpcontabil.com.br-key.pem
copy C:\Certbot\live\portal.jpcontabil.com.br\chain.pem C:\Certificados\portaljp\portal.jpcontabil.com.br-chain.pem
```

#### Aplicar Novo Certificado

```batch
# Graceful restart (não derruba conexões)
C:\xampp\apache\bin\httpd.exe -k graceful
```

#### Automatizar Renovação (Task Scheduler)

```batch
# Criar tarefa agendada (executar como administrador)
schtasks /create /tn "Certbot Renewal" /tr "C:\Program Files\Certbot\certbot.exe renew --post-hook \"C:\xampp\apache\bin\httpd.exe -k graceful\"" /sc daily /st 03:00
```

**Parâmetros:**
- `/tn`: Nome da tarefa
- `/tr`: Comando a executar
- `/sc daily`: Executar diariamente
- `/st 03:00`: Horário (3:00 AM)
- `--post-hook`: Comando após renovação (restart Apache)

### Método 2: win-acme

**Download:** https://www.win-acme.com/

```batch
# Executar wacs.exe
wacs.exe

# Seguir wizard:
# 1. Create certificate
# 2. Manual input
# 3. Domain: portal.jpcontabil.com.br
# 4. Validation: HTTP
# 5. Installation: Apache
# 6. Path: C:\xampp\apache\conf\extra\httpd-vhosts.conf
```

**win-acme cria tarefa agendada automaticamente.**

### Verificar Certificado Após Renovação

```batch
# Ver data de expiração
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -noout -enddate

# Ver detalhes completos
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -text -noout
```

**Testar no browser:**
1. Acessar `https://portal.jpcontabil.com.br`
2. Clicar no cadeado 🔒
3. Ver data de expiração

**Testar online:**
- https://www.ssllabs.com/ssltest/analyze.html?d=portal.jpcontabil.com.br

---

## Adicionar Novos VirtualHosts

### Exemplo: Adicionar Subdomínio `api.jpcontabil.com.br`

#### 1. Editar httpd-vhosts.conf

```batch
notepad C:\xampp\apache\conf\extra\httpd-vhosts.conf
```

#### 2. Adicionar VirtualHost HTTP

```apache
# VirtualHost HTTP - Redirect para HTTPS
<VirtualHost *:80>
    ServerName api.jpcontabil.com.br

    RewriteEngine On
    RewriteCond %{HTTPS} off
    RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]
</VirtualHost>
```

#### 3. Adicionar VirtualHost HTTPS

```apache
# VirtualHost HTTPS - Proxy para API Node.js
<VirtualHost *:443>
    ServerName api.jpcontabil.com.br

    # SSL Configuration
    SSLEngine on
    SSLCertificateFile "C:/Certificados/api/api.jpcontabil.com.br-crt.pem"
    SSLCertificateKeyFile "C:/Certificados/api/api.jpcontabil.com.br-key.pem"
    SSLCertificateChainFile "C:/Certificados/api/api.jpcontabil.com.br-chain.pem"

    # Proxy para API Node.js na porta 3000
    ProxyPreserveHost On
    ProxyTimeout 300
    ProxyPass / http://127.0.0.1:3000/
    ProxyPassReverse / http://127.0.0.1:3000/

    # Logs específicos para este VirtualHost
    ErrorLog "C:/xampp/apache/logs/api-error.log"
    CustomLog "C:/xampp/apache/logs/api-access.log" combined
</VirtualHost>
```

#### 4. Obter Certificado SSL

```batch
# Parar Apache
net stop Apache2.4

# Obter certificado para api.jpcontabil.com.br
certbot certonly --standalone -d api.jpcontabil.com.br

# Copiar certificados
copy C:\Certbot\live\api.jpcontabil.com.br\fullchain.pem C:\Certificados\api\api.jpcontabil.com.br-crt.pem
copy C:\Certbot\live\api.jpcontabil.com.br\privkey.pem C:\Certificados\api\api.jpcontabil.com.br-key.pem
copy C:\Certbot\live\api.jpcontabil.com.br\chain.pem C:\Certificados\api\api.jpcontabil.com.br-chain.pem

# Iniciar Apache
net start Apache2.4
```

#### 5. Testar Configuração

```batch
# Testar sintaxe
C:\xampp\apache\bin\httpd.exe -t

# Ver VirtualHosts configurados
C:\xampp\apache\bin\httpd.exe -t -D DUMP_VHOSTS
```

**Saída esperada:**
```
VirtualHost configuration:
*:80                   api.jpcontabil.com.br (C:/xampp/apache/conf/extra/httpd-vhosts.conf:X)
*:443                  api.jpcontabil.com.br (C:/xampp/apache/conf/extra/httpd-vhosts.conf:Y)
*:80                   portal.jpcontabil.com.br (...)
*:443                  portal.jpcontabil.com.br (...)
```

#### 6. Aplicar Mudanças

```batch
C:\xampp\apache\bin\httpd.exe -k graceful
```

#### 7. Atualizar DNS

No painel de controle do domínio (Registro.br, GoDaddy, etc.):
```
Tipo: A
Nome: api
Valor: [IP do servidor]
TTL: 3600
```

#### 8. Testar

```batch
# Via curl
curl -I https://api.jpcontabil.com.br

# Via browser
start https://api.jpcontabil.com.br
```

---

## Backup de Configurações

### Backup Manual

```batch
# Criar pasta de backup com timestamp
set timestamp=%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set timestamp=%timestamp: =0%

# Criar estrutura de backup
mkdir C:\backups\apache\%timestamp%

# Copiar configurações
xcopy C:\xampp\apache\conf C:\backups\apache\%timestamp%\conf\ /E /I

# Copiar certificados
xcopy C:\Certificados C:\backups\apache\%timestamp%\certificados\ /E /I

# Criar arquivo de info
echo Backup criado em: %date% %time% > C:\backups\apache\%timestamp%\info.txt
echo Versao Apache: >> C:\backups\apache\%timestamp%\info.txt
C:\xampp\apache\bin\httpd.exe -v >> C:\backups\apache\%timestamp%\info.txt
```

### Script PowerShell de Backup Automatizado

**Salvar como:** `C:\scripts\backup-apache.ps1`

```powershell
# Configuração
$date = Get-Date -Format "yyyy-MM-dd_HHmmss"
$backupRoot = "C:\backups\apache"
$backupPath = "$backupRoot\$date"
$retentionDays = 30  # Manter backups por 30 dias

# Criar pasta de backup
New-Item -ItemType Directory -Path $backupPath -Force | Out-Null

# Copiar configurações
Copy-Item -Path "C:\xampp\apache\conf" -Destination "$backupPath\conf" -Recurse -Force

# Copiar certificados
Copy-Item -Path "C:\Certificados" -Destination "$backupPath\certificados" -Recurse -Force

# Criar arquivo de informações
$info = @"
Backup criado em: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Computador: $env:COMPUTERNAME
Usuário: $env:USERNAME
Versão Apache: $(& "C:\xampp\apache\bin\httpd.exe" -v | Select-Object -First 1)
"@
$info | Out-File -FilePath "$backupPath\info.txt"

# Comprimir backup
Compress-Archive -Path $backupPath -DestinationPath "$backupPath.zip" -Force
Remove-Item -Path $backupPath -Recurse -Force

# Limpar backups antigos
Get-ChildItem -Path $backupRoot -Filter "*.zip" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$retentionDays) } |
    Remove-Item -Force

Write-Host "Backup concluído: $backupPath.zip"
```

### Agendar Backup Automático

```batch
# Criar tarefa agendada (semanal, domingo às 2:00 AM)
schtasks /create /tn "Apache Backup" /tr "powershell.exe -File C:\scripts\backup-apache.ps1" /sc weekly /d SUN /st 02:00 /ru SYSTEM
```

### Restaurar Backup

```batch
# Parar Apache
net stop Apache2.4

# Restaurar configurações
xcopy C:\backups\apache\2026-02-13_120000\conf\ C:\xampp\apache\conf\ /E /I /Y

# Restaurar certificados
xcopy C:\backups\apache\2026-02-13_120000\certificados\ C:\Certificados\ /E /I /Y

# Testar configuração
C:\xampp\apache\bin\httpd.exe -t

# Iniciar Apache
net start Apache2.4
```

---

## Leitura e Análise de Logs

### Localização dos Logs

```
C:\xampp\apache\logs\
├── access.log           ← Todas as requisições HTTP
├── error.log            ← Erros e warnings
├── ssl_request.log      ← Requisições SSL (se habilitado)
├── portal-error.log     ← Erros do VirtualHost portal (se configurado)
└── portal-access.log    ← Acessos do VirtualHost portal (se configurado)
```

### Access Log (access.log)

**Formato:** Combined Log Format

```
200.150.100.50 - - [13/Feb/2026:10:15:30 -0300] "GET /tasks HTTP/1.1" 200 15432 "https://portal.jpcontabil.com.br/" "Mozilla/5.0..."
```

**Campos:**
1. **IP do cliente:** 200.150.100.50
2. **Identd:** - (não usado)
3. **Usuário autenticado:** - (se usar HTTP auth)
4. **Timestamp:** [13/Feb/2026:10:15:30 -0300]
5. **Requisição:** "GET /tasks HTTP/1.1"
6. **Status HTTP:** 200
7. **Bytes enviados:** 15432
8. **Referer:** "https://portal.jpcontabil.com.br/"
9. **User-Agent:** "Mozilla/5.0..."

#### Análises Úteis

**Contar requisições por status code:**
```batch
findstr " 200 " C:\xampp\apache\logs\access.log | find /c /v ""
findstr " 404 " C:\xampp\apache\logs\access.log | find /c /v ""
findstr " 500 " C:\xampp\apache\logs\access.log | find /c /v ""
```

**Top 10 IPs com mais requisições:**
```powershell
Get-Content C:\xampp\apache\logs\access.log |
    ForEach-Object { ($_ -split " ")[0] } |
    Group-Object |
    Sort-Object Count -Descending |
    Select-Object -First 10 Count, Name
```

**Requisições mais lentas (via mod_logio):**
```powershell
Get-Content C:\xampp\apache\logs\access.log |
    Where-Object { $_ -match "\d+$" } |
    ForEach-Object {
        $bytes = [int]($_ -split " ")[-1]
        [PSCustomObject]@{
            Bytes = $bytes
            Line = $_
        }
    } |
    Sort-Object Bytes -Descending |
    Select-Object -First 10
```

**URLs mais acessadas:**
```powershell
Get-Content C:\xampp\apache\logs\access.log |
    ForEach-Object { ($_ -match '"(GET|POST) ([^ ]+)') ? $Matches[2] : $null } |
    Where-Object { $_ } |
    Group-Object |
    Sort-Object Count -Descending |
    Select-Object -First 20 Count, Name
```

### Error Log (error.log)

**Formato:**
```
[Thu Feb 13 10:15:30.123456 2026] [proxy:error] [pid 1234:tid 5678] (OS 10061) No connection could be made...
```

**Campos:**
1. **Timestamp:** [Thu Feb 13 10:15:30.123456 2026]
2. **Módulo:** [proxy:error]
3. **PID/TID:** [pid 1234:tid 5678]
4. **Mensagem:** (OS 10061) No connection could be made...

#### Análises Úteis

**Ver apenas erros (não warnings):**
```batch
findstr "[error]" C:\xampp\apache\logs\error.log
```

**Ver erros de proxy:**
```batch
findstr "[proxy:error]" C:\xampp\apache\logs\error.log
```

**Ver erros SSL:**
```batch
findstr "[ssl:error]" C:\xampp\apache\logs\error.log
```

**Últimas 50 linhas:**
```powershell
Get-Content C:\xampp\apache\logs\error.log -Tail 50
```

**Monitorar em tempo real:**
```powershell
Get-Content C:\xampp\apache\logs\error.log -Wait -Tail 20
```

### Rotação de Logs

**Problema:** Logs crescem infinitamente

**Solução:** Rotação automática

#### Script PowerShell de Rotação

**Salvar como:** `C:\scripts\rotate-apache-logs.ps1`

```powershell
$date = Get-Date -Format "yyyy-MM-dd"
$logsPath = "C:\xampp\apache\logs"

# Parar Apache
Stop-Service Apache2.4

# Renomear logs atuais
Rename-Item "$logsPath\access.log" "$logsPath\access-$date.log" -ErrorAction SilentlyContinue
Rename-Item "$logsPath\error.log" "$logsPath\error-$date.log" -ErrorAction SilentlyContinue

# Criar novos logs vazios
New-Item "$logsPath\access.log" -ItemType File -Force
New-Item "$logsPath\error.log" -ItemType File -Force

# Iniciar Apache
Start-Service Apache2.4

# Comprimir logs antigos
Get-ChildItem -Path $logsPath -Filter "*-$date.log" |
    ForEach-Object {
        Compress-Archive -Path $_.FullName -DestinationPath "$($_.FullName).zip"
        Remove-Item $_.FullName
    }

# Remover logs comprimidos com mais de 90 dias
Get-ChildItem -Path $logsPath -Filter "*.zip" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } |
    Remove-Item -Force

Write-Host "Rotação de logs concluída"
```

#### Agendar Rotação (mensal)

```batch
schtasks /create /tn "Apache Log Rotation" /tr "powershell.exe -File C:\scripts\rotate-apache-logs.ps1" /sc monthly /d 1 /st 03:00 /ru SYSTEM
```

---

## Monitoramento

### mod_status (Monitoramento Interno)

#### Habilitar mod_status

```apache
# httpd.conf
LoadModule status_module modules/mod_status.so

# Configurar acesso
<Location "/server-status">
    SetHandler server-status
    Require local  # Apenas localhost
    # Ou: Require ip 192.168.1.0/24  # Rede local
</Location>
```

#### Acessar Status

```
http://localhost/server-status
http://localhost/server-status?auto  # Formato texto (para scripts)
```

**Métricas exibidas:**
- Uptime do servidor
- Requests per second
- Bytes per second / Bytes per request
- Threads ocupados vs idle
- Conexões ativas

### Script de Monitoramento

**Salvar como:** `C:\scripts\monitor-apache.ps1`

```powershell
# Verificar se Apache está rodando
$service = Get-Service Apache2.4 -ErrorAction SilentlyContinue

if ($service.Status -ne 'Running') {
    Write-Host "⚠️ ALERTA: Apache não está rodando!" -ForegroundColor Red
    # Enviar email de alerta (opcional)
    # Send-MailMessage -To "ti@jpcontabil.com.br" -From "monitor@jpcontabil.com.br" -Subject "Apache Down" -Body "Apache parou de funcionar" -SmtpServer "smtp.gmail.com"
    exit 1
}

# Verificar se portas estão abertas
$port80 = Test-NetConnection -ComputerName localhost -Port 80 -InformationLevel Quiet
$port443 = Test-NetConnection -ComputerName localhost -Port 443 -InformationLevel Quiet

if (-not $port80) {
    Write-Host "⚠️ ALERTA: Porta 80 não está respondendo!" -ForegroundColor Red
}

if (-not $port443) {
    Write-Host "⚠️ ALERTA: Porta 443 não está respondendo!" -ForegroundColor Red
}

# Verificar certificado SSL
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2("C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem")
$daysLeft = ($cert.NotAfter - (Get-Date)).Days

if ($daysLeft -lt 30) {
    Write-Host "⚠️ ALERTA: Certificado SSL expira em $daysLeft dias!" -ForegroundColor Yellow
}

# Verificar tamanho dos logs
$errorLogSize = (Get-Item "C:\xampp\apache\logs\error.log").Length / 1MB
$accessLogSize = (Get-Item "C:\xampp\apache\logs\access.log").Length / 1MB

if ($errorLogSize -gt 100) {
    Write-Host "⚠️ AVISO: error.log está com $([math]::Round($errorLogSize, 2)) MB. Considere rotação." -ForegroundColor Yellow
}

if ($accessLogSize -gt 500) {
    Write-Host "⚠️ AVISO: access.log está com $([math]::Round($accessLogSize, 2)) MB. Considere rotação." -ForegroundColor Yellow
}

# Status OK
Write-Host "✅ Apache está funcionando corretamente" -ForegroundColor Green
Write-Host "  - Serviço: $($service.Status)"
Write-Host "  - Porta 80: $($port80 ? 'OK' : 'FALHA')"
Write-Host "  - Porta 443: $($port443 ? 'OK' : 'FALHA')"
Write-Host "  - SSL expira em: $daysLeft dias"
Write-Host "  - error.log: $([math]::Round($errorLogSize, 2)) MB"
Write-Host "  - access.log: $([math]::Round($accessLogSize, 2)) MB"
```

#### Agendar Monitoramento (a cada 15 minutos)

```batch
schtasks /create /tn "Apache Monitor" /tr "powershell.exe -File C:\scripts\monitor-apache.ps1" /sc minute /mo 15 /ru SYSTEM
```

---

## Troubleshooting Comum

### Apache não inicia após atualização de config

**Causa:** Erro de sintaxe na configuração

**Solução:**
```batch
# Ver erro específico
C:\xampp\apache\bin\httpd.exe -t

# Restaurar backup
xcopy C:\backups\apache\[ultimo_backup]\conf\ C:\xampp\apache\conf\ /E /I /Y
```

### Certificado SSL não é reconhecido

**Causa:** Chain file faltando ou incorreto

**Verificar:**
```batch
# Verificar certificado
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -text -noout

# Verificar chain
C:\xampp\apache\bin\openssl.exe crl2pkcs7 -nocrl -certfile C:\Certificados\portaljp\portal.jpcontabil.com.br-chain.pem | C:\xampp\apache\bin\openssl.exe pkcs7 -print_certs -noout
```

**Solução:**
```apache
# httpd-vhosts.conf
SSLCertificateChainFile "C:/Certificados/portaljp/portal.jpcontabil.com.br-chain.pem"
```

### Logs crescendo muito rápido

**Causa:** Tráfego alto ou ataques

**Análise:**
```powershell
# Ver IPs com mais requisições
Get-Content C:\xampp\apache\logs\access.log |
    ForEach-Object { ($_ -split " ")[0] } |
    Group-Object |
    Sort-Object Count -Descending |
    Select-Object -First 10
```

**Solução:**
- Implementar rate limiting (mod_ratelimit)
- Bloquear IPs maliciosos no firewall
- Configurar rotação de logs mais frequente

### Performance degradada

**Diagnóstico:**
```batch
# Ver threads em uso
C:\xampp\apache\bin\httpd.exe -M | findstr mpm

# Ver status (se mod_status habilitado)
curl http://localhost/server-status?auto
```

**Soluções:**
- Aumentar ThreadsPerChild (httpd-mpm.conf)
- Verificar queries lentas no Flask/MySQL
- Habilitar cache (mod_cache)

---

## Conclusão

Este guia cobre as principais tarefas de manutenção do Apache:

✅ Gerenciamento do serviço (iniciar, parar, reiniciar)
✅ Atualização segura de configurações
✅ Renovação de certificados SSL
✅ Adição de novos VirtualHosts
✅ Backup e restore de configurações
✅ Leitura e análise de logs
✅ Monitoramento proativo
✅ Troubleshooting de problemas comuns

**Próximos passos:**
1. Configurar backups automáticos (semanal)
2. Configurar rotação de logs (mensal)
3. Configurar monitoramento (15 minutos)
4. Configurar renovação automática de SSL (diária)

**Dicas:**
- Sempre fazer backup antes de alterar configurações
- Sempre testar sintaxe antes de reiniciar (`httpd.exe -t`)
- Preferir `graceful restart` a `hard restart`
- Monitorar logs regularmente para detectar problemas
- Renovar certificados SSL com antecedência (30+ dias)

---

**Última atualização:** Fevereiro 2026
**Mantido por:** Equipe TI - JP Contábil
