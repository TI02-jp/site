# Sincronização de Encerramento Fiscal - Guia de Uso

## Visão Geral

Este documento explica como usar a funcionalidade de sincronização automática do campo "Encerramento Fiscal" no inventário, que busca dados da API da Acessorias.

## Como Funciona

O sistema busca entregas de "Fechamento Fiscal" na API da Acessorias para o período de **01/01/2026 a 31/01/2026** (quando as entregas do fechamento de dezembro/2025 são realizadas) e marca automaticamente o campo "Encerramento Fiscal" como **"Sim"** para empresas que atendem aos seguintes critérios:

### Critérios de Matching:

1. **Nome da Entrega**: Deve ser exatamente "Fechamento Fiscal" (case-insensitive)
2. **Status da Entrega**:
   - Deve ter data de entrega válida (`EntDtEntrega` diferente de vazio ou "0000-00-00"), OU
   - Status deve conter "ent." ou "entreg" (case-insensitive)
3. **Comentário/Protocolo**: Deve conter "OK" ou "SEM MOVIMENTO" em campos como:
   - Comentarios, ComentariosEntrega, Protocolo, EntProtocolo, EntComentarios, EntGuiaLida, Observacoes, etc.
4. **Data de Referência**: A data da entrega deve estar entre 01/01/2026 e 31/01/2026

### Importante:
- A sincronização **apenas marca como "Sim"**, nunca desmarca empresas que já estão marcadas
- Empresas que já têm `encerramento_fiscal = True` são puladas para economizar chamadas à API

---

## Ferramentas Disponíveis

### 1. Testar Conexão API (Botão na UI)

**Onde:** Página de Inventário > Botão "🛡️ Testar Conexão API"

**O que faz:**
- Verifica se o token da API está configurado
- Testa conexão com a API da Acessorias
- Usa a primeira empresa ativa do banco para fazer um teste real
- Exibe resultado da conexão

**Quando usar:**
- Antes de fazer a primeira sincronização
- Quando houver erros de conexão
- Para verificar se o token está válido

**Resultado esperado:**
```
✓ Conexão com API bem-sucedida!

Token: Configurado e válido
CNPJ de teste: 12345678901234
Empresa de teste: EMPRESA TESTE LTDA
Entregas encontradas: 15
```

---

### 2. Sincronizar Encerramento (Botão na UI)

**Onde:** Página de Inventário > Botão "☁️ Sincronizar Encerramento (API)"

**O que faz:**
- Busca todas as empresas ativas no inventário
- Para cada empresa com CNPJ válido (14 dígitos):
  - Chama API da Acessorias
  - Busca entregas de "Fechamento Fiscal"
  - Atualiza o campo `encerramento_fiscal` conforme critérios
- Mostra resumo completo da sincronização

**Quando usar:**
- Quando precisar atualizar os dados de encerramento fiscal
- Mensalmente (ou conforme necessidade)
- Após adicionar novas empresas ao inventário

**Resultado esperado:**
```
✓ Sincronização concluída!

Empresas verificadas: 150
Atualizadas: 45
  - Marcadas como SIM: 45
  - Marcadas como NÃO: 0

⚠ Empresas puladas (CNPJ inválido): 3
```

**Se houver erros:**
```
❌ Erros em 2 empresa(s):
  • EMPRESA XYZ LTDA: Erro de rede ao consultar entregas
  • EMPRESA ABC SA: CNPJ não encontrado ou sem entregas no período
  ... (veja o console para detalhes)
```

---

### 3. Script de Teste Standalone

**Onde:** `scripts/test_acessorias_api.py`

**O que faz:**
- Testa a API da Acessorias diretamente via linha de comando
- Não depende do banco de dados
- Mostra resposta completa da API
- Analisa cada entrega e explica por que passou ou não nos critérios

**Como usar:**
```bash
# Ativar ambiente virtual (se usar)
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Executar script com CNPJ (apenas números)
python scripts/test_acessorias_api.py 12345678901234
```

**Quando usar:**
- Para testar um CNPJ específico isoladamente
- Para ver a resposta bruta da API
- Para debugar problemas de matching
- Para validar que a API está retornando dados corretos

**Exemplo de saída:**
```
================================================================================
TESTE DA API DA ACESSORIAS - ROTA /DELIVERIES
================================================================================

✓ Token configurado: 63303e0bd4...915e7ed5
✓ Base URL: https://api.acessorias.com

📡 Fazendo requisição para:
  URL: https://api.acessorias.com/deliveries/12345678901234/
  Parâmetros: {'DtInitial': '2025-12-01', 'DtFinal': '2025-12-31'}

📥 Resposta recebida:
  Status Code: 200

✓ Resposta JSON recebida

📦 Total de entregas encontradas: 15

🔍 ANÁLISE DAS ENTREGAS:
--------------------------------------------------------------------------------

Entrega #1:
  Nome: Fechamento Fiscal
  Status: Entregue
  Datas:
    EntCompetencia: 2025-12-31
    EntDtPrazo: 2025-12-15
    EntDtEntrega: 2025-12-15
    EntDtAtraso: 2025-12-15
  Data de referência parseada: 2025-12-15
  Data dentro do período? True
  Critérios de match:
    ✓ Nome exatamente 'Fechamento Fiscal'? True
    ✓ Status indica entregue? True
    ✓ Comentário OK ou SEM MOVIMENTO? True
    ✓ Data no período? True
  🎯 MATCH! Esta entrega atende aos critérios de Encerramento Fiscal

[... outras entregas ...]

📊 RESUMO:
  Total de entregas: 15
  Fechamento Fiscal encontrado? ✓ SIM

✓ SUCESSO: Encerramento Fiscal será marcado como SIM
```

---

### 4. Endpoint de Debug Individual

**Onde:** `/api/inventario/debug-encerramento/<empresa_id>`

**O que faz:**
- Testa a sincronização para UMA empresa específica
- Retorna JSON completo com:
  - Dados da empresa
  - CNPJ usado
  - Resposta bruta da API
  - Resultado do matching
  - Razão da decisão

**Como usar:**

**No navegador ou Postman:**
```
GET http://localhost:9000/api/inventario/debug-encerramento/123
```
(substitua 123 pelo ID da empresa)

**Com curl:**
```bash
curl -X GET "http://localhost:9000/api/inventario/debug-encerramento/123" \
     -H "Cookie: session=sua_sessao_aqui"
```

**Quando usar:**
- Para debugar por que uma empresa específica não está sendo marcada corretamente
- Para ver exatamente o que a API retorna para aquela empresa
- Para validar o CNPJ de uma empresa

**Exemplo de resposta:**
```json
{
  "success": true,
  "empresa": {
    "id": 123,
    "razao_social": "EMPRESA TESTE LTDA"
  },
  "cnpj": "12345678901234",
  "period": {
    "start": "2025-12-01",
    "end": "2025-12-31"
  },
  "api_response": {
    "total_entregas": 15,
    "entregas": [...]
  },
  "match": {
    "found": true,
    "encerramento_fiscal": true,
    "details": {
      "nome": "Fechamento Fiscal",
      "status": "Entregue",
      "referencia": "2025-12-15",
      "raw": {...}
    }
  }
}
```

---

## Configuração Necessária

### Variáveis de Ambiente (.env)

O sistema busca o token de autenticação nas seguintes variáveis (em ordem de prioridade):

1. `ACESSORIAS_DELIVERIES_TOKEN` (recomendado)
2. `ACESSORIAS_TOKEN`
3. `ACESSORIAS_API_TOKEN`

**Exemplo:**
```env
ACESSORIAS_DELIVERIES_TOKEN=63303e0bd46822d5af8d24ff915e7ed5
```

### Base URL da API (opcional)

Por padrão: `https://api.acessorias.com`

Para usar outra URL:
```env
ACESSORIAS_BASE=https://api-homolog.acessorias.com
```

---

## Logs e Diagnóstico

### Verificar Logs do Servidor

Os logs detalhados são gravados em:
- Console do servidor Flask
- Arquivo de log (se configurado)

**Logs importantes:**
- `INFO`: Empresas processadas, matches encontrados
- `WARNING`: Empresas puladas (CNPJ inválido)
- `ERROR`: Erros ao buscar entregas, token inválido
- `DEBUG`: Detalhes de paginação, análise de entregas

**Exemplo de log bem-sucedido:**
```
INFO - Buscando entregas para empresa {'empresa_id': 123, 'cnpj': '12345678901234', ...}
DEBUG - Buscando pagina 1 de entregas {'identificador': '12345678901234', 'page': 1, ...}
INFO - Encerramento Fiscal encontrado {'empresa_id': 123, 'entrega_nome': 'Fechamento Fiscal', ...}
INFO - Sincronizacao de encerramento fiscal concluida {'checked': 150, 'updated': 45, ...}
```

**Exemplo de log com erro:**
```
WARNING - Empresa pulada: CNPJ invalido {'empresa_id': 456, 'cnpj_raw': '123456', ...}
ERROR - Erro ao buscar entregas para empresa {'empresa_id': 789, 'error': 'Timeout', ...}
```

### Console do Navegador

Erros detalhados também são logados no console do navegador (F12 > Console):
- Erros de rede
- Respostas da API
- Array completo de erros se houver múltiplas falhas

---

## Troubleshooting

### Problema: "Token inválido ou expirado"

**Causa:** Token da API não configurado ou incorreto

**Solução:**
1. Verificar `.env`: confirmar que `ACESSORIAS_DELIVERIES_TOKEN` está definido
2. Validar token com administrador da Acessorias
3. Testar com botão "Testar Conexão API"

---

### Problema: "Empresas puladas (CNPJ inválido): X"

**Causa:** Empresas com CNPJ mal formatado ou incompleto

**Solução:**
1. Verificar logs do servidor para ver quais empresas foram puladas
2. Corrigir CNPJs no cadastro de empresas
3. CNPJ deve ter exatamente 14 dígitos numéricos

**Verificar no log:**
```
WARNING - Empresa pulada: CNPJ invalido {
  'empresa_id': 123,
  'razao_social': 'EMPRESA XYZ',
  'cnpj_raw': '12.345.678',
  'cnpj_limpo': '12345678'
}
```

---

### Problema: "Atualizados: 0" (nenhuma empresa atualizada)

**Possíveis causas:**

1. **Todas as empresas já estão marcadas como "Sim"**
   - Sistema não desmarca, apenas marca como "Sim"
   - Se já sincronizou antes, empresas permanecem marcadas

2. **API não retorna entregas no período**
   - Usar script de teste para verificar: `python scripts/test_acessorias_api.py <cnpj>`
   - Confirmar que período está correto (dezembro/2025)

3. **Entregas não atendem aos critérios**
   - Nome não é exatamente "Fechamento Fiscal"
   - Status não indica "entregue"
   - Comentário não contém "OK" ou "SEM MOVIMENTO"
   - Data fora do período

**Diagnóstico:**
```bash
# Testar um CNPJ específico
python scripts/test_acessorias_api.py 12345678901234

# Ver resposta completa da API e análise de cada entrega
```

---

### Problema: Erro de rede / timeout

**Causa:** Problemas de conectividade ou firewall

**Solução:**
1. Verificar conectividade com internet
2. Testar acesso manual: `curl https://api.acessorias.com`
3. Verificar proxy/firewall corporativo
4. Aumentar timeout (se necessário) em `acessorias_deliveries.py`

---

### Problema: "CNPJ não encontrado ou sem entregas no período"

**Causa:** CNPJ não existe na base da Acessorias OU não há entregas no período especificado

**Solução:**
1. Confirmar que CNPJ está correto e ativo na Acessorias
2. Verificar se existem entregas para dezembro/2025
3. Usar script de teste para ver resposta da API

---

## Alteração de Período

Para sincronizar outro período (não dezembro/2025):

### Opção 1: Temporário (somente uma sincronização)

Não implementado na UI atual, mas pode ser adicionado se necessário.

### Opção 2: Permanente (alterar código)

**Arquivo:** `app/services/inventario_sync.py`

**Linhas 22-23:**
```python
DEFAULT_PERIOD_START = date(2025, 12, 1)  # Alterar aqui
DEFAULT_PERIOD_END = date(2025, 12, 31)   # Alterar aqui
```

**Nota:** Após alterar, reiniciar o servidor Flask.

---

## Perguntas Frequentes (FAQ)

### 1. Com que frequência devo sincronizar?

Depende da frequência de atualizações na API da Acessorias. Recomendação: mensal ou conforme necessidade.

### 2. A sincronização vai desmarcar empresas?

**Não.** A sincronização apenas marca como "Sim", nunca desmarca. Se uma empresa já tem `encerramento_fiscal = True`, ela é pulada.

### 3. Quanto tempo leva a sincronização?

Depende do número de empresas:
- ~150 empresas: 1-3 minutos
- ~500 empresas: 5-10 minutos

### 4. Posso sincronizar apenas uma empresa?

Sim, use o endpoint de debug:
```
GET /api/inventario/debug-encerramento/<empresa_id>
```

### 5. Como ver quais empresas foram atualizadas?

Verificar logs do servidor ou banco de dados:
```sql
SELECT e.id, e.razao_social, i.encerramento_fiscal
FROM tbl_empresas e
JOIN tbl_inventario i ON i.empresa_id = e.id
WHERE i.encerramento_fiscal = 1;
```

### 6. O que fazer se o token expirar?

1. Solicitar novo token ao administrador da Acessorias
2. Atualizar `.env` com novo token
3. Reiniciar servidor Flask
4. Testar com botão "Testar Conexão API"

---

## Arquivos Importantes

- **`app/services/acessorias_deliveries.py`** - Cliente da API
- **`app/services/inventario_sync.py`** - Lógica de sincronização
- **`app/controllers/routes/blueprints/empresas.py`** - Endpoints da API (linhas 1459-1708)
- **`app/templates/empresas/inventario.html`** - Interface do usuário
- **`scripts/test_acessorias_api.py`** - Script de teste standalone
- **`.env`** - Configurações (token, base URL)

---

## Suporte

Em caso de problemas:

1. **Verificar logs do servidor** (INFO, WARNING, ERROR)
2. **Usar script de teste** para um CNPJ específico
3. **Testar conexão API** via botão na UI
4. **Consultar este documento** para troubleshooting
5. **Verificar console do navegador** (F12)

Para questões técnicas sobre a API da Acessorias, consultar a documentação oficial ou suporte da Acessorias.
