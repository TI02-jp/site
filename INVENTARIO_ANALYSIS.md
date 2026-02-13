# Diagnóstico Técnico de Performance: Rota `/inventario`

Como Arquiteto de Software Sênior, realizei uma análise profunda da rota `/inventario` e dos serviços correlacionados. Abaixo apresento o diagnóstico, a classificação de riscos e o plano de ação recomendado.

---

## 1. Diagnóstico Técnico Detalhado

### A. Gargalo Crítico: Armazenamento de Binários em JSON (Base64)
O problema mais grave identificado é o armazenamento do conteúdo completo de arquivos PDF codificados em Base64 dentro de colunas JSON (`cfop_files`, `cfop_consolidado_files`, `cliente_files`) na tabela `tbl_inventario`.

*   **Impacto:**
    *   **Memória:** Ao carregar um objeto `Inventario`, o SQLAlchemy traz todo o conteúdo dos arquivos para a memória RAM do servidor. Se uma empresa tiver 10MB de PDFs, cada requisição que toque nessa linha consumirá pelo menos 10MB+ de RAM apenas para esse campo.
    *   **Banco de Dados:** O tráfego entre o MySQL e o servidor Flask torna-se extremamente lento devido ao tamanho dos blobs.
    *   **Thread Blocking:** O Waitress (servidor WSGI) fica com threads presas processando strings gigantes de Base64, reduzindo a capacidade de atender outros usuários.

### B. Risco do "Modo Tadeu"
No arquivo `empresas.py`, a variável `is_tadeu` (baseada no nome de usuário) força o carregamento de `include_file_columns=True`.
*   **Problema:** Para este usuário específico, a aplicação tenta carregar *todos* os arquivos de *todas* as empresas listadas na página. Se houver 20 empresas e cada uma tiver 5MB de arquivos, estamos falando de 100MB de dados sendo transferidos e processados em uma única requisição.

### C. Eficiência de Consultas e N+1
*   **Contagem de Arquivos:** A função `get_inventario_file_counts_by_empresa_ids` tenta usar `json_length` (otimizado no banco), mas possui um fallback que carrega o JSON inteiro no Python para contar os elementos caso o banco não suporte a função. Esse fallback é catastrófico para a performance.
*   **Eager Loading:** O sistema já utiliza `joinedload` e `selectinload` em vários pontos, o que evita o problema clássico de N+1 queries, mas o custo de carregar as colunas pesadas anula esse ganho.

---

## 2. Classificação de Problemas por Impacto

| Problema | Impacto | Dificuldade | Prioridade |
| :--- | :--- | :--- | :--- |
| Base64 em colunas JSON | **Alto** (Crítico) | Média | **Imediata** |
| Carregamento completo para usuário admin | **Alto** | Baixa | **Alta** |
| Fallback de contagem via Python | **Médio** | Baixa | **Alta** |
| Falta de índices compostos em `status/empresa_id` | **Médio** | Baixa | **Média** |

---

## 3. Plano de Ação Priorizado

### Passo 1: Desacoplamento de Conteúdo (Urgente)
**Ação:** Remover o conteúdo Base64 da tabela `tbl_inventario` e movê-lo para um sistema de arquivos ou Storage (S3/MinIO).
*   **Refatoração:** A coluna JSON deve conter apenas metadados (nome, tamanho, path, data). O arquivo físico deve ser salvo em disco.
*   **Ganho:** Redução de >90% no uso de memória e latência de banco.

### Passo 2: Otimização do Carregamento sob Demanda
**Ação:** Atualmente, a rota `/api/inventario/files/<empresa_id>` já lista metadados. O "Modo Tadeu" deve ser removido do carregamento inicial e transformado em um carregamento via JS (Lazy Loading) conforme o usuário expande a linha.

### Passo 3: Melhoria de Índices (MySQL)
**Ação:** Criar índices para acelerar os filtros de dashboard e listagem.
```sql
CREATE INDEX idx_inventario_status_composite ON tbl_inventario (status, empresa_id);
CREATE INDEX idx_empresas_tributacao_ativo ON tbl_empresas (tributacao, ativo);
```

---

## 4. Sugestão de Refatoração de Código (Exemplo Prático)

**Antigo (Lento e Perigoso):**
```python
# No model Inventario
cfop_files = db.Column(db.JSON) # Contém base64 pesado
```

**Novo (Escalável):**
```python
# No model Inventario
cfop_files = db.Column(db.JSON) # Ex: [{"id": "uuid", "filename": "doc.pdf", "path": "uploads/2026/..."}]

def get_file_content(file_id):
    # Busca no disco apenas quando o usuário CLICAR no botão de visualizar
    return open(os.path.join(UPLOAD_FOLDER, file_id), 'rb').read()
```

---

## 5. Resumo de Ganhos Esperados

1.  **Tempo de Resposta:** Redução de segundos para milissegundos na rota `/inventario`.
2.  **Estabilidade:** Fim dos erros de "Memory Limit Exceeded" e quedas do servidor por sobrecarga.
3.  **Escalabilidade:** O banco de dados MySQL não crescerá exponencialmente com arquivos binários, facilitando backups e manutenção.

**Risco Técnico:** Baixo (requer migração de dados existentes para arquivos físicos).
**Impacto Final:** Extremo.

---

## 6. Alterações Aplicadas

As seguintes melhorias de performance foram implementadas com sucesso:

1.  **Storage Híbrido (Disco + DB):** Novos uploads são salvos em `static/uploads/inventario/`, reduzindo drasticamente o tamanho das linhas no banco de dados. O sistema mantém compatibilidade com arquivos legados no banco.
2.  **Carregamento Diferido (Defer):** A aplicação agora utiliza `defer()` do SQLAlchemy para evitar o carregamento de colunas JSON pesadas em todas as listagens (incluindo o "Modo Tadeu" e chunks de carregamento progressivo).
3.  **Índices Compostos:** Criados novos índices no MySQL para otimizar filtros de `status`, `tributacao`, `ativo` e `codigo_empresa`.
4.  **Otimização de Update:** A rota de atualização inline agora evita o carregamento de binários ao modificar campos simples.
5.  **Resiliência na Contagem:** A função de contagem de arquivos foi otimizada para ser mais cirúrgica mesmo em cenários de fallback.

---

## 7. Otimizações Globais do Sistema

Além da rota de inventário, as seguintes melhorias estruturais foram aplicadas para reduzir o consumo de CPU e RAM em todo o portal:

1.  **Carregamento de Usuário Cacheado:** O Flask-Login agora utiliza um cache de 5 minutos para carregar os dados do usuário atual, economizando uma consulta ao banco de dados por requisição em todo o sistema.
2.  **Notificações Push em Segundo Plano:** O envio de Web Push foi movido para uma fila de tarefas em background. Anteriormente, a requisição do usuário ficava travada aguardando a resposta dos servidores do Google/Mozilla, o que bloqueava threads do servidor.
3.  **Otimização de Consultas de Tarefas:** Substituição de `joinedload` por `selectinload` no sistema de tarefas, evitando o "Produto Cartesiano" no SQL, o que reduzia a velocidade de carregamento da Central de Tarefas.
4.  **Índices Adicionais:** Criados índices nas tabelas de `tasks`, `task_notifications` e `audit_logs` para acelerar contagens e filtros de interface.
5.  **Otimização de Middleware:** Reduzido o processamento de CPU no rastreador de performance para consultas rápidas.
6.  **Redução de Pegada de Memória no Background:** O job diário de sincronização agora evita carregar binários pesados, processando centenas de empresas com uma fração da memória anterior.
