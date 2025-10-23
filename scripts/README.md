# Scripts Utilitários - Portal JP

Este diretório contém scripts de setup e manutenção do portal.

---

## 📂 Estrutura

```
scripts/
├── setup/              # Scripts de instalação one-time
│   ├── generate_vapid_keys.py      # Gera chaves VAPID para push notifications
│   └── install_redis_windows.ps1   # Instala e configura Redis no Windows
│
└── maintenance/        # Scripts de manutenção periódica
    └── cleanup_logs.py             # Limpa e rotaciona logs automaticamente
```

---

## 🔧 Scripts de Setup (Executar uma vez)

### `setup/generate_vapid_keys.py`

**Finalidade**: Gera par de chaves VAPID (públicas/privadas) para Web Push Notifications

**Quando usar**:
- Primeira instalação do sistema
- Ao configurar push notifications pela primeira vez
- Se as chaves foram comprometidas e precisam ser regeneradas

**Como executar**:
```bash
cd C:\Users\ti02\Desktop\site
python scripts/setup/generate_vapid_keys.py
```

**Output**:
- Chaves impressas no console
- Adicionar ao `.env`:
  ```
  VAPID_PUBLIC_KEY=...
  VAPID_PRIVATE_KEY=...
  ```

---

### `setup/install_redis_windows.ps1`

**Finalidade**: Instala e configura Redis Server no Windows

**Quando usar**:
- Primeira instalação do portal em servidor Windows
- Ao adicionar cache Redis ao sistema
- Para rate limiting e sessões distribuídas

**Como executar** (PowerShell como Admin):
```powershell
cd C:\Users\ti02\Desktop\site
.\scripts\setup\install_redis_windows.ps1
```

**O que faz**:
1. Baixa Redis para Windows (via Memurai ou MSOpenTech)
2. Instala como serviço Windows
3. Configura para iniciar automaticamente
4. Testa conexão

**Após instalação**:
- Adicionar ao `.env`:
  ```
  REDIS_URL=redis://localhost:6379/0
  RATELIMIT_STORAGE_URI=redis://localhost:6379/1
  ```

---

## 🔄 Scripts de Manutenção (Executar periodicamente)

### `maintenance/cleanup_logs.py`

**Finalidade**: Limpa e rotaciona logs do portal para evitar crescimento descontrolado

**Quando usar**:
- **Manualmente**: Quando logs ultrapassarem 10MB
- **Automaticamente**: Agendar no Task Scheduler (semanal)
- **Após travamentos**: Para liberar espaço em disco

**Como executar**:

⚠️ **IMPORTANTE**: Parar o Waitress antes de executar!

```bash
# 1. Parar Waitress (usar restart_simple.bat ou taskkill)
taskkill /PID <PID_DO_WAITRESS> /F

# 2. Executar limpeza
cd C:\Users\ti02\Desktop\site
python scripts/maintenance/cleanup_logs.py

# 3. Reiniciar Waitress
python run.py
```

**O que faz**:
1. Verifica tamanho dos logs (`app.log`, `error.log`)
2. Se > 5MB:
   - Arquiva linhas antigas em `logs/archive/app_YYYYMMDD_HHMMSS.log`
   - Mantém apenas últimas 1000 linhas no log ativo
3. Remove arquivos de arquivo com > 30 dias

**Configuração** (dentro do script):
```python
KEEP_LINES = 1000        # Linhas a manter no log ativo
days_to_keep = 30        # Dias de retenção de arquivos
```

**Agendar no Task Scheduler** (Recomendado):

```powershell
# Criar tarefa semanal (domingo 3h da manhã)
$action = New-ScheduledTaskAction -Execute "C:\Users\ti02\Desktop\site\venv\Scripts\python.exe" `
    -Argument "C:\Users\ti02\Desktop\site\scripts\maintenance\cleanup_logs.py"

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am

Register-ScheduledTask -TaskName "Portal JP - Cleanup Logs" `
    -Action $action -Trigger $trigger -Description "Limpa logs do Portal JP semanalmente"
```

---

## 📊 Status dos Logs Atuais

Para verificar tamanho dos logs:

```bash
ls -lh logs/
```

**Tamanhos recomendados**:
- ✅ < 5MB: OK, não precisa limpar
- ⚠️ 5-15MB: Recomendado limpar
- ❌ > 15MB: **Urgente** - limpar imediatamente

---

## 🚀 Próximos Scripts a Criar (Sugestões)

### `maintenance/backup_db.py`
- Backup automático do MySQL
- Compressão e upload para storage externo
- Rotação de backups (manter últimos 30 dias)

### `maintenance/check_health.py`
- Verificação de saúde do sistema
- MySQL, Redis, disk space, CPU
- Enviar alertas se algo estiver errado

### `setup/migrate_production.sh`
- Script de deploy para produção
- Git pull + migrations + restart

---

## 📝 Notas

- Todos os scripts possuem logging detalhado
- Erros são capturados e logados (não travam o sistema)
- Verificações de segurança antes de operações destrutivas
- Documentação inline (docstrings) em cada função

---

## 🆘 Suporte

Problemas com scripts?
1. Verificar logs: `python <script> 2>&1 | tee script_output.log`
2. Verificar permissões: Executar como Admin se necessário
3. Verificar dependências: `pip install -r requirements.txt`

---

**Última atualização**: 23/Outubro/2025
