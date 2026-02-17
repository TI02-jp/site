# Documentação Técnica - Portal JP Contábil

Este diretório contém a documentação técnica completa do Portal JP Contábil.

---

## Índice de Documentos

### 📘 [PROXY_REVERSO.md](PROXY_REVERSO.md)
**Documentação completa da arquitetura de proxy reverso Apache + Flask**

Explica em detalhes:
- O que é proxy reverso e por que usamos
- Arquitetura completa do sistema (diagramas incluídos)
- Por que Apache nas portas 80/443 e Flask na porta 9000
- Fluxo detalhado de uma requisição (passo a passo)
- Configuração do serviço Windows
- Configurações críticas do Apache e Flask
- Redirecionamento HTTP para HTTPS
- Certificados SSL (Let's Encrypt)
- Segurança em múltiplas camadas
- Otimizações de performance
- Troubleshooting completo

**Público:** Desenvolvedores, administradores de sistemas, novos membros da equipe

---

### 🔧 [MANUTENCAO_APACHE.md](MANUTENCAO_APACHE.md)
**Guia prático de manutenção do Apache**

Cobre tarefas operacionais:
- Comandos rápidos de gerenciamento
- Como reiniciar Apache (graceful vs hard restart)
- Atualização de configurações (workflow seguro)
- Renovação de certificados SSL (Certbot)
- Adicionar novos VirtualHosts
- Backup e restore de configurações
- Análise de logs (access.log, error.log)
- Monitoramento proativo
- Scripts PowerShell prontos para uso

**Público:** Administradores de sistemas, equipe de TI

---

## Documentos Relacionados

### Raiz do Projeto

- **[API_DOCUMENTATION.md](../API_DOCUMENTATION.md)** - Documentação da API RESTful v1
- **[README.md](../README.md)** - Informações gerais do projeto
- **[.env](.env)** - Variáveis de ambiente (não versionado)

### Configuração do Apache

- **C:\xampp\apache\conf\httpd.conf** - Configuração principal do Apache
- **C:\xampp\apache\conf\extra\httpd-vhosts.conf** - VirtualHosts (proxy reverso)
- **C:\xampp\apache\conf\extra\httpd-ssl.conf** - Configurações SSL
- **C:\Certificados\portaljp\\*** - Certificados SSL Let's Encrypt

---

## Visão Geral da Arquitetura

```
┌─────────────┐
│   Cliente   │ (Navegador/App)
│   Browser   │
└──────┬──────┘
       │ HTTPS (porta 443)
       │
       ▼
┌──────────────────────┐
│  Apache HTTP Server  │ (Proxy Reverso)
│   Portas: 80 + 443   │
│   - SSL/TLS          │
│   - Compressão       │
│   - Cache            │
└──────────┬───────────┘
           │ HTTP (localhost)
           │
           ▼
┌──────────────────────┐
│ Waitress WSGI Server │ (Porta 9000)
│   - Threading        │
│   - Proxy headers    │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Flask Application   │ (Python)
│   - Lógica negócio   │
│   - API REST         │
│   - Templates        │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   MySQL Database     │ (Porta 3306)
│   - Persistência     │
└──────────────────────┘
```

---

## Checklist Rápido

### Iniciar o Sistema

```batch
# 1. Iniciar Apache
net start Apache2.4

# 2. Iniciar aplicação Flask
cd c:\Users\ti02\Desktop\site-teste
python run.py

# 3. Verificar funcionamento
curl https://portal.jpcontabil.com.br/health
```

### Troubleshooting Rápido

**Apache não inicia:**
```batch
# Ver erro
C:\xampp\apache\bin\httpd.exe -t

# Ver logs
type C:\xampp\apache\logs\error.log
```

**Aplicação não responde:**
```batch
# Verificar se Flask está rodando
netstat -ano | findstr :9000

# Ver logs da aplicação
type c:\Users\ti02\Desktop\site-teste\logs\app.log
```

**Erro 502 Bad Gateway:**
- Flask não está rodando na porta 9000
- Porta no ProxyPass está errada (verificar httpd-vhosts.conf)

---

## Configurações Críticas

### Porta do Proxy Reverso

⚠️ **ATENÇÃO:** Existe um conflito de configuração identificado:

- **Apache ProxyPass:** Configurado para `http://127.0.0.1:5000`
- **Flask .env:** `WAITRESS_PORT=9000`

**Correção necessária:**

Editar `C:\xampp\apache\conf\extra\httpd-vhosts.conf` linha 51-52:
```apache
# Alterar de:
ProxyPass / http://127.0.0.1:5000/ retry=0 timeout=300 acquire=300 keepalive=On
ProxyPassReverse / http://127.0.0.1:5000/

# Para:
ProxyPass / http://127.0.0.1:9000/ retry=0 timeout=300 acquire=300 keepalive=On
ProxyPassReverse / http://127.0.0.1:9000/
```

Após alterar:
```batch
C:\xampp\apache\bin\httpd.exe -k graceful
```

---

## Certificado SSL

**Domínio:** portal.jpcontabil.com.br
**Provedor:** Let's Encrypt (R13)
**Validade:** 12/01/2026 - 12/04/2026 (90 dias)
**Renovação:** Necessária a cada 60-80 dias

**Verificar expiração:**
```batch
C:\xampp\apache\bin\openssl.exe x509 -in C:\Certificados\portaljp\portal.jpcontabil.com.br-crt.pem -noout -enddate
```

**Renovar:**
```batch
certbot renew --standalone --post-hook "C:\xampp\apache\bin\httpd.exe -k graceful"
```

---

## Logs

**Localização:** `C:\xampp\apache\logs\`

- **error.log** - Erros e warnings do Apache
- **access.log** - Todas as requisições HTTP
- **portal-error.log** - Erros específicos do VirtualHost portal (se configurado)
- **portal-access.log** - Acessos específicos do VirtualHost portal (se configurado)

**Ver em tempo real:**
```powershell
Get-Content C:\xampp\apache\logs\error.log -Wait -Tail 50
```

---

## Scripts Úteis

### Backup Automático

**Script:** `C:\scripts\backup-apache.ps1` (ver [MANUTENCAO_APACHE.md](MANUTENCAO_APACHE.md))

**Agendar:**
```batch
schtasks /create /tn "Apache Backup" /tr "powershell.exe -File C:\scripts\backup-apache.ps1" /sc weekly /d SUN /st 02:00
```

### Monitoramento

**Script:** `C:\scripts\monitor-apache.ps1` (ver [MANUTENCAO_APACHE.md](MANUTENCAO_APACHE.md))

**Agendar:**
```batch
schtasks /create /tn "Apache Monitor" /tr "powershell.exe -File C:\scripts\monitor-apache.ps1" /sc minute /mo 15
```

### Renovação SSL

**Agendar:**
```batch
schtasks /create /tn "Certbot Renewal" /tr "certbot renew --post-hook \"C:\xampp\apache\bin\httpd.exe -k graceful\"" /sc daily /st 03:00
```

---

## Suporte

**Documentação oficial:**
- Apache: https://httpd.apache.org/docs/2.4/
- Waitress: https://docs.pylonsproject.org/projects/waitress/
- Flask: https://flask.palletsprojects.com/
- Let's Encrypt: https://letsencrypt.org/docs/

**Equipe:**
- TI JP Contábil: ti02@jpcontabil.com.br

---

**Última atualização:** Fevereiro 2026
**Versão do portal:** v2.0.4
