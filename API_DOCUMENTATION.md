# JP Contábil Portal - Documentação da API

> **Base URL:** `/api/v1`
> **Autenticação:** Bearer Token via header `Authorization`
> **Formato:** JSON
> **CSRF:** Desabilitado para endpoints da API

---

## Sumário

- [Autenticação](#autenticação)
- [Tags](#tags)
- [Usuários](#usuários)
- [Tarefas](#tarefas)
  - [CRUD de Tarefas](#crud-de-tarefas)
  - [Status da Tarefa](#status-da-tarefa)
  - [Seguidores](#seguidores)
  - [Histórico](#histórico)
  - [Comentários](#comentários)
  - [Anexos](#anexos)
- [Notificações](#notificações)
- [Comunicados](#comunicados)
- [Reuniões](#reuniões)
- [Empresas](#empresas)
- [Departamentos](#departamentos)
- [Procedimentos Operacionais](#procedimentos-operacionais)
- [Notas de Débito](#notas-de-débito)
- [Cadastro de Notas](#cadastro-de-notas)
- [Notas Recorrentes](#notas-recorrentes)
- [Cursos](#cursos)
- [Categorias de Cursos](#categorias-de-cursos)
- [Links de Acesso](#links-de-acesso)
- [FAQ / Inclusões](#faq--inclusões)
- [Diretoria - Eventos](#diretoria---eventos)
- [Diretoria - Acordos](#diretoria---acordos)
- [Diretoria - Feedbacks](#diretoria---feedbacks)
- [Permissões de Relatórios](#permissões-de-relatórios)
- [Códigos de Erro](#códigos-de-erro)

---

## Autenticação

A API utiliza **Bearer Token** assinado com `itsdangerous`. O token é obtido via login e tem validade de **24 horas** (86400s).

**Header obrigatório em todas as rotas protegidas:**
```
Authorization: Bearer <token>
```

---

### `POST /auth/login`

Autentica o usuário e retorna o token. **Rate limit: 10 req/min.**

**Request Body:**
```json
{
  "username": "username",
  "password": "senha123"
}
```
> `username` aceita tanto o username quanto o email.

**Response `200`:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiJ9...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "id": 1,
    "username": "joao.silva",
    "name": "João Silva",
    "email": "joao@jpcontabil.com",
    "role": "admin",
    "tags": [
      { "id": 1, "nome": "Fiscal" },
      { "id": 2, "nome": "Contábil" }
    ]
  }
}
```

**Erros:**

| Status | Erro | Descrição |
|--------|------|-----------|
| `400` | `username_or_email_and_password_required` | Campos obrigatórios ausentes |
| `401` | `invalid_credentials` | Usuário ou senha incorretos |
| `403` | `inactive_user` | Usuário inativo |
| `429` | — | Rate limit excedido |

---

### `POST /auth/refresh`

Re-emite um novo token para o usuário autenticado.

**Response `200`:**
```json
{
  "token": "novo_token_assinado...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": { ... }
}
```

---

### `GET /me`

Retorna os dados do perfil do usuário autenticado.

**Response `200`:**
```json
{
  "id": 1,
  "username": "joao.silva",
  "name": "João Silva",
  "email": "joao@jpcontabil.com",
  "role": "admin",
  "tags": [
    { "id": 1, "nome": "Fiscal" }
  ]
}
```

---

## Tags

### `GET /tags`

Lista tags acessíveis ao usuário. Admins veem todas; usuários comuns veem apenas suas próprias tags.

**Response `200`:**
```json
[
  { "id": 1, "nome": "Fiscal" },
  { "id": 2, "nome": "Contábil" },
  { "id": 3, "nome": "Pessoal" }
]
```

---

## Usuários

### `GET /users`

Lista usuários ativos para atribuição de tarefas. Não-admins veem apenas usuários com tags compartilhadas. Limite: 200.

**Response `200`:**
```json
[
  {
    "id": 1,
    "name": "João Silva",
    "username": "joao.silva",
    "email": "joao@jpcontabil.com"
  },
  {
    "id": 2,
    "name": "Maria Santos",
    "username": "maria.santos",
    "email": "maria@jpcontabil.com"
  }
]
```

---

## Tarefas

### CRUD de Tarefas

#### `GET /tasks`

Lista tarefas do usuário (limite: 200). Admins veem todas; não-admins veem apenas as criadas ou atribuídas a si.

**Query Parameters:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `status` | string | Filtrar por status: `pending`, `in_progress`, `completed`, `cancelled` |

**Response `200`:**
```json
[
  {
    "id": 42,
    "title": "Revisar apuração fiscal",
    "description": "Verificar lançamentos do mês de janeiro",
    "status": "pending",
    "priority": "medium",
    "due_date": "2026-03-15",
    "created_at": "2026-01-10T14:30:00",
    "updated_at": "2026-01-12T09:00:00",
    "tag": { "id": 1, "nome": "Fiscal" },
    "created_by": 1,
    "assigned_to": 2,
    "assignee_name": "Maria Santos",
    "attachments": [
      {
        "id": 10,
        "name": "documento.pdf",
        "mime_type": "application/pdf",
        "url": "/static/uploads/tasks/abc123.pdf"
      }
    ]
  }
]
```

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `invalid_status` |

---

#### `GET /tasks/{taskId}`

Retorna detalhes de uma tarefa específica.

**Response `200`:** Objeto `Task` (mesmo formato da listagem).

**Erros:**

| Status | Erro |
|--------|------|
| `403` | `forbidden` |
| `404` | `not_found` |

---

#### `POST /tasks`

Cria uma nova tarefa.

**Request Body:**
```json
{
  "title": "Revisar apuração fiscal",
  "description": "Verificar os lançamentos do mês",
  "tag_id": 1,
  "assigned_to": 2,
  "priority": "high",
  "due_date": "2026-03-15"
}
```

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|:-----------:|-----------|
| `title` | string | Sim | Título da tarefa |
| `tag_id` | integer | Sim | ID da tag/setor |
| `description` | string | Não | Descrição detalhada |
| `assigned_to` | integer | Não | ID do usuário atribuído |
| `priority` | string | Não | `low`, `medium` (padrão), `high` |
| `due_date` | string | Não | Data de vencimento (`YYYY-MM-DD`) |

**Response `201`:** Objeto `Task` criado.

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `title_and_tag_id_required`, `invalid_priority`, `invalid_due_date` |
| `403` | `forbidden_for_tag` |
| `404` | `tag_not_found`, `assignee_not_found` |
| `500` | `failed_to_create_task` |

---

#### `PATCH /tasks/{taskId}`

Atualiza campos da tarefa. Envie apenas os campos que deseja alterar.

**Request Body:**
```json
{
  "title": "Título atualizado",
  "status": "in_progress",
  "priority": "high",
  "due_date": "2026-04-01"
}
```

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `title` | string | Novo título |
| `description` | string | Nova descrição |
| `status` | string | `pending`, `in_progress`, `completed`, `cancelled` |
| `priority` | string | `low`, `medium`, `high` |
| `due_date` | string/null | Data (`YYYY-MM-DD`) ou `null` para remover |

**Response `200`:** Objeto `Task` atualizado.

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `title_cannot_be_empty`, `invalid_status`, `invalid_priority`, `invalid_due_date` |
| `403` | `forbidden` |
| `404` | `not_found` |
| `500` | `failed_to_update_task` |

---

#### `DELETE /tasks/{taskId}`

Exclui uma tarefa. Permitido para admin, criador ou assignee.

**Response `204`:** Sem conteúdo.

**Erros:**

| Status | Erro |
|--------|------|
| `403` | `forbidden` |
| `404` | `not_found` |
| `500` | `failed_to_delete_task` |

---

### Status da Tarefa

#### `POST /tasks/{taskId}/status`

Atualiza apenas o status de uma tarefa.

**Request Body:**
```json
{
  "status": "completed"
}
```

**Response `200`:** Objeto `Task` atualizado.

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `invalid_status` |
| `403` | `forbidden` |
| `404` | `not_found` |

---

### Seguidores

#### `GET /tasks/{taskId}/followers`

Lista seguidores de uma tarefa.

**Response `200`:**
```json
[
  {
    "user_id": 3,
    "name": "Carlos Oliveira",
    "username": "carlos.oliveira"
  }
]
```

---

#### `POST /tasks/{taskId}/followers`

Adiciona um seguidor a uma tarefa.

**Request Body:**
```json
{
  "user_id": 3
}
```

**Response `200`:**
```json
{
  "status": "added",
  "user_id": 3
}
```

> Se o usuário já é seguidor, retorna `{ "status": "already_following" }`.

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `user_id_required` |
| `404` | `user_not_found` |
| `500` | `failed_to_add_follower` |

---

#### `DELETE /tasks/{taskId}/followers/{userId}`

Remove um seguidor de uma tarefa.

**Response `200`:**
```json
{
  "deleted": 1
}
```

---

### Histórico

#### `GET /tasks/{taskId}/history`

Retorna o histórico de alterações de status de uma tarefa.

**Response `200`:**
```json
[
  {
    "id": 1,
    "task_id": 42,
    "from_status": "pending",
    "to_status": "in_progress",
    "changed_at": "2026-01-12T09:00:00",
    "changed_by": 1
  },
  {
    "id": 2,
    "task_id": 42,
    "from_status": "in_progress",
    "to_status": "completed",
    "changed_at": "2026-01-15T16:30:00",
    "changed_by": 2
  }
]
```

---

### Comentários

#### `GET /tasks/{taskId}/comments`

Retorna os comentários de uma tarefa, ordenados por data de criação.

**Response `200`:**
```json
[
  {
    "id": 5,
    "task_id": 42,
    "author_id": 1,
    "author_name": "João Silva",
    "body": "Tarefa revisada e aprovada.",
    "created_at": "2026-01-13T10:00:00"
  }
]
```

---

#### `POST /tasks/{taskId}/comments`

Adiciona um comentário a uma tarefa.

**Request Body:**
```json
{
  "body": "Verificar pendências antes de finalizar."
}
```

**Response `201`:** Objeto `Comment` criado.

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `body_required` |
| `500` | `failed_to_create_comment` |

---

### Anexos

#### `POST /tasks/{taskId}/attachments`

Faz upload de um arquivo e anexa a uma tarefa.

**Content-Type:** `multipart/form-data`

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|:-----------:|-----------|
| `file` | binary | Sim | Arquivo a ser anexado |

**Response `201`:**
```json
{
  "id": 10,
  "name": "relatorio.pdf",
  "mime_type": "application/pdf",
  "url": "/static/uploads/tasks/abc123def.pdf"
}
```

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `file_required` |
| `500` | `failed_to_upload` |

---

## Notificações

### `GET /notifications`

Lista notificações recentes do usuário com contagem de não lidas.

**Query Parameters:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `limit` | integer | 50 | Máximo de resultados (1–200) |

**Response `200`:**
```json
{
  "notifications": [
    {
      "id": 100,
      "type": "task",
      "message": "Nova tarefa atribuída a você",
      "task_id": 42,
      "announcement_id": null,
      "created_at": "2026-02-10T08:00:00",
      "is_read": false
    }
  ],
  "unread": 5
}
```

---

### `POST /notifications/read`

Marca notificações como lidas. Envie `notification_id` para marcar uma específica, ou omita para marcar todas.

**Request Body:**
```json
{
  "notification_id": 100
}
```

**Response `200`:**
```json
{
  "updated": 1
}
```

---

## Comunicados

### `GET /announcements`

Lista comunicados ordenados por data (mais recentes primeiro).

**Query Parameters:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `limit` | integer | 20 | Máximo de resultados (1–100) |

**Response `200`:**
```json
[
  {
    "id": 10,
    "date": "2026-02-01",
    "subject": "Comunicado importante sobre prazos",
    "content": "<p>Informamos que os prazos foram alterados...</p>",
    "attachments": [
      {
        "id": 1,
        "name": "anexo.pdf",
        "url": "/static/uploads/announcements/file.pdf",
        "mime_type": "application/pdf"
      }
    ],
    "created_at": "2026-02-01T10:00:00",
    "updated_at": null
  }
]
```

---

### `POST /announcements` | Admin

Cria um novo comunicado.

**Request Body:**
```json
{
  "subject": "Novo comunicado",
  "content": "Conteúdo do comunicado em HTML",
  "date": "2026-02-13"
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `subject` | string | Sim |
| `content` | string | Sim |
| `date` | string (`YYYY-MM-DD`) | Sim |

**Response `201`:** Objeto `Announcement` criado.

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `subject_content_date_required`, `invalid_date` |
| `403` | `forbidden` |

---

### `GET /announcements/{announcementId}`

Retorna detalhes de um comunicado específico.

---

### `PATCH /announcements/{announcementId}` | Admin

Atualiza um comunicado existente. Envie apenas os campos que deseja alterar.

**Request Body:**
```json
{
  "subject": "Título atualizado",
  "content": "Novo conteúdo",
  "date": "2026-02-15"
}
```

---

### `DELETE /announcements/{announcementId}` | Admin

Exclui um comunicado. **Response `204`.**

---

### `POST /announcements/{announcementId}/read`

Marca o comunicado como lido para o usuário atual.

**Response `200`:**
```json
{
  "updated": 1
}
```

---

## Reuniões

### `GET /reunioes`

Retorna reuniões da sala com status atualizado via Google Calendar.

> O header `X-Calendar-Fallback` pode indicar `primary-cache` ou `stale-cache` se os dados vieram de cache.

**Response `200`:** Array de objetos de evento de reunião.

---

### `GET /calendario-eventos`

Retorna eventos do calendário geral de colaboradores.

**Response `200`:** Array de objetos de evento do calendário.

---

## Empresas

### `GET /empresas`

Lista empresas cadastradas com busca e filtro de campos.

**Query Parameters:**

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| `q` | string | — | Busca por nome ou CNPJ (LIKE) |
| `limit` | integer | 100 | Máximo de resultados (1–300) |
| `fields` | string | — | Campos para retornar, separados por vírgula. Ex: `id,nome,cnpj` |

**Campos disponíveis para `fields`:** `id`, `nome`, `cnpj`, `data_abertura`, `socio_administrador`, `tributacao`, `codigo_empresa`, `ativo`

**Response `200`:**
```json
[
  {
    "id": 1,
    "nome": "Empresa Exemplo LTDA",
    "cnpj": "12345678000199",
    "data_abertura": "2020-01-15",
    "socio_administrador": "José da Silva",
    "tributacao": "Lucro Presumido",
    "codigo_empresa": "001",
    "ativo": true
  }
]
```

**Erros:**

| Status | Erro |
|--------|------|
| `400` | `invalid_fields` (retorna lista de campos inválidos e permitidos) |

---

### `GET /empresas/{empresaId}`

Retorna detalhes de uma empresa incluindo departamentos.

**Response `200`:**
```json
{
  "id": 1,
  "nome": "Empresa Exemplo LTDA",
  "cnpj": "12345678000199",
  "data_abertura": "2020-01-15",
  "socio_administrador": "José da Silva",
  "tributacao": "Lucro Presumido",
  "codigo_empresa": "001",
  "ativo": true,
  "departamentos": [
    {
      "id": 1,
      "empresa_id": 1,
      "tipo": "fiscal",
      "responsavel": "Maria Santos",
      "descricao": null,
      "formas_importacao": null,
      "forma_movimento": null,
      "envio_digital": null,
      "envio_fisico": null,
      "malote_coleta": null,
      "observacao_movimento": null,
      "observacao_importacao": null,
      "observacao_contato": null,
      "updated_at": "2026-01-10T14:00:00"
    }
  ]
}
```

---

### `POST /empresas` | Admin

Cria uma nova empresa.

**Request Body:**
```json
{
  "nome": "Nova Empresa LTDA",
  "cnpj": "98765432000111",
  "data_abertura": "2025-06-01",
  "codigo_empresa": "042",
  "atividade_principal": "Comércio varejista",
  "socio_administrador": "Carlos Oliveira",
  "tributacao": "Simples Nacional",
  "regime_lancamento": "Caixa",
  "sistemas_consultorias": "Sistema XYZ",
  "sistema_utilizado": "ERP ABC",
  "acessos": "Portal e-CAC, SEFAZ",
  "observacao_acessos": "Certificado A1 vencendo em março",
  "contatos": "(11) 99999-0000",
  "ativo": true
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `nome` | string | Sim |
| `cnpj` | string | Sim |
| `data_abertura` | string (`YYYY-MM-DD`) | Sim |
| `codigo_empresa` | string | Sim |
| Demais campos | string/boolean | Não |

**Response `201`:** Objeto `Empresa` criado com departamentos.

---

### `PATCH /empresas/{empresaId}` | Admin

Atualiza dados de uma empresa. Envie apenas os campos a alterar.

**Request Body:**
```json
{
  "nome_empresa": "Nome Atualizado LTDA",
  "tributacao": "Lucro Real",
  "ativo": false
}
```

**Campos atualizáveis:** `nome_empresa`, `cnpj`, `data_abertura`, `codigo_empresa`, `atividade_principal`, `socio_administrador`, `tributacao`, `regime_lancamento`, `sistemas_consultorias`, `sistema_utilizado`, `acessos`, `observacao_acessos`, `contatos`, `ativo`

**Response `200`:** Objeto `Empresa` atualizado.

---

### `DELETE /empresas/{empresaId}` | Admin

Exclui uma empresa. **Response `204`.**

---

## Departamentos

### `GET /empresas/{empresaId}/departamentos`

Lista departamentos de uma empresa.

**Response `200`:** Array de objetos `Departamento`.

---

### `POST /departamentos` | Admin

Cria um novo departamento.

**Request Body:**
```json
{
  "empresa_id": 1,
  "tipo": "fiscal",
  "responsavel": "Maria Santos",
  "descricao": "Departamento fiscal",
  "formas_importacao": "XML",
  "forma_movimento": "Mensal",
  "envio_digital": "Sim",
  "envio_fisico": "Não",
  "malote_coleta": "Semanal"
}
```

| Campo | Tipo | Obrigatório | Valores |
|-------|------|:-----------:|---------|
| `empresa_id` | integer | Sim | — |
| `tipo` | string | Sim | `fiscal`, `contabil`, `financeiro`, `pessoal`, `administrativo` |
| Demais campos | string | Não | — |

**Response `201`:** Objeto `Departamento` criado.

---

### `PATCH /departamentos/{departamentoId}` | Admin

Atualiza um departamento existente.

**Response `200`:** Objeto `Departamento` atualizado.

---

### `DELETE /departamentos/{departamentoId}` | Admin

Exclui um departamento. **Response `204`.**

---

## Procedimentos Operacionais

### `GET /procedimentos`

Lista procedimentos operacionais.

**Response `200`:**
```json
[
  {
    "id": 1,
    "title": "Procedimento de Apuração",
    "descricao": "Passo a passo da apuração fiscal",
    "created_by": 1,
    "created_at": "2026-01-05T10:00:00",
    "updated_at": null
  }
]
```

---

### `GET /procedimentos/{procedimentoId}`

Retorna detalhes de um procedimento.

---

### `POST /procedimentos`

Cria um novo procedimento.

**Request Body:**
```json
{
  "title": "Novo procedimento",
  "descricao": "Descrição detalhada do procedimento"
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `title` | string | Sim |
| `descricao` | string | Não |

**Response `201`:** Objeto `Procedure` criado.

---

### `PATCH /procedimentos/{procedimentoId}`

Atualiza um procedimento. **Response `200`.**

---

### `DELETE /procedimentos/{procedimentoId}`

Exclui um procedimento. **Response `204`.**

---

## Notas de Débito

### `GET /notas/debito`

Lista notas de débito.

**Response `200`:**
```json
[
  {
    "id": 1,
    "data_emissao": "2026-01-15",
    "empresa": "Empresa ABC",
    "notas": "NF 12345",
    "qtde_itens": 5,
    "valor_un": 150.00,
    "total": 750.00,
    "acordo": null,
    "forma_pagamento": "PIX",
    "observacao": null,
    "created_at": "2026-01-15T08:00:00"
  }
]
```

---

### `GET /notas/debito/{notaId}`

Retorna detalhes de uma nota de débito.

---

### `POST /notas/debito`

Cria uma nova nota de débito.

**Request Body:**
```json
{
  "data_emissao": "2026-02-01",
  "empresa": "Empresa XYZ",
  "notas": "NF 67890",
  "qtde_itens": 3,
  "valor_un": 200.00,
  "acordo": "Mensal",
  "forma_pagamento": "Transferência",
  "observacao": "Referente ao mês de janeiro"
}
```

**Response `201`:** Objeto `NotaDebito` criado.

---

### `PATCH /notas/debito/{notaId}`

Atualiza uma nota de débito. **Response `200`.**

---

### `DELETE /notas/debito/{notaId}`

Exclui uma nota de débito. **Response `204`.**

---

## Cadastro de Notas

> Todos os endpoints deste módulo requerem **permissão de admin**.

### `GET /notas/cadastro`

Lista cadastros de notas.

**Response `200`:**
```json
[
  {
    "id": 1,
    "pix": "chave@pix.com",
    "cadastro": "Empresa ABC",
    "valor": 500.00,
    "acordo": "Mensal",
    "forma_pagamento": "PIX",
    "usuario": "usuario_sistema",
    "senha": "***",
    "ativo": true,
    "created_at": "2026-01-10T09:00:00"
  }
]
```

---

### `GET /notas/cadastro/{cadastroId}`

Retorna detalhes de um cadastro.

---

### `POST /notas/cadastro`

Cria um novo cadastro de nota.

**Request Body:**
```json
{
  "pix": "chave@pix.com",
  "cadastro": "Empresa Nova",
  "valor": 800.00,
  "acordo": "Trimestral",
  "forma_pagamento": "Boleto",
  "usuario": "user_sys",
  "senha": "senha_acesso",
  "ativo": true
}
```

**Response `201`:** Objeto `CadastroNota` criado.

---

### `PATCH /notas/cadastro/{cadastroId}`

Atualiza um cadastro. **Response `200`.**

---

### `DELETE /notas/cadastro/{cadastroId}`

Exclui um cadastro. **Response `204`.**

---

## Notas Recorrentes

> Todos os endpoints deste módulo requerem **permissão de admin**.

### `GET /notas/recorrentes`

Lista notas recorrentes.

**Response `200`:**
```json
[
  {
    "id": 1,
    "empresa": "Empresa Mensal LTDA",
    "descricao": "Serviço mensal de contabilidade",
    "valor": 1200.00,
    "periodo_inicio": "01/2026",
    "periodo_fim": "12/2026",
    "dia_emissao": 15,
    "forma_pagamento": "Boleto",
    "observacao": null,
    "created_at": "2026-01-05T10:00:00"
  }
]
```

---

### `GET /notas/recorrentes/{notaId}`

Retorna detalhes de uma nota recorrente.

---

### `POST /notas/recorrentes`

Cria uma nova nota recorrente.

**Request Body:**
```json
{
  "empresa": "Empresa Nova LTDA",
  "descricao": "Honorários contábeis",
  "valor": 2500.00,
  "periodo_inicio": "03/2026",
  "periodo_fim": "02/2027",
  "dia_emissao": 10,
  "forma_pagamento": "PIX",
  "observacao": "Contrato anual"
}
```

**Response `201`:** Objeto `NotaRecorrente` criado.

---

### `PATCH /notas/recorrentes/{notaId}`

Atualiza uma nota recorrente. **Response `200`.**

---

### `DELETE /notas/recorrentes/{notaId}`

Exclui uma nota recorrente. **Response `204`.**

---

## Cursos

### `GET /courses`

Lista cursos de capacitação.

**Response `200`:**
```json
[
  {
    "id": 1,
    "name": "IRPF 2026",
    "instructor": "Dr. Carlos",
    "sectors": "Fiscal, Contábil",
    "participants": "João, Maria",
    "workload": "08:00:00",
    "start_date": "2026-02-01",
    "schedule_start": "09:00:00",
    "schedule_end": "17:00:00",
    "completion_date": "2026-02-05",
    "status": "Concluído",
    "observation": null,
    "tags": [
      { "id": 1, "name": "Tributário" }
    ]
  }
]
```

---

### `POST /courses`

Cria um novo curso.

**Request Body:**
```json
{
  "name": "Novo Curso de Capacitação",
  "instructor": "Prof. Ana",
  "sectors": "Fiscal",
  "start_date": "2026-04-01",
  "schedule_start": "08:00",
  "schedule_end": "12:00",
  "status": "Agendado"
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `name` | string | Sim |
| Demais campos | string | Não |

**Response `201`:** Objeto `Course` criado.

---

### `PATCH /courses/{courseId}`

Atualiza dados de um curso. **Response `200`.**

---

### `DELETE /courses/{courseId}`

Exclui um curso. **Response `204`.**

---

## Categorias de Cursos

### `GET /course-tags`

Lista categorias de cursos.

**Response `200`:**
```json
[
  { "id": 1, "name": "Tributário" },
  { "id": 2, "name": "Contabilidade" }
]
```

---

### `POST /course-tags`

Cria uma nova categoria.

**Request Body:**
```json
{
  "name": "Trabalhista"
}
```

**Response `201`:** Objeto `CourseTag` criado.

---

### `DELETE /course-tags/{tagId}`

Exclui uma categoria. **Response `204`.**

---

## Links de Acesso

### `GET /acessos`

Lista links de acesso rápido.

**Response `200`:**
```json
[
  {
    "id": 1,
    "category": "Sistemas",
    "label": "Portal e-CAC",
    "url": "https://cav.receita.fazenda.gov.br",
    "description": "Centro de Atendimento Virtual da Receita Federal",
    "created_by_id": 1,
    "created_at": "2026-01-01T10:00:00",
    "updated_at": null
  }
]
```

---

### `POST /acessos`

Cria um novo link de acesso.

**Request Body:**
```json
{
  "category": "Sistemas",
  "label": "Novo Link",
  "url": "https://exemplo.com",
  "description": "Descrição do link"
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `label` | string | Sim |
| `url` | string | Sim |
| `category` | string | Não |
| `description` | string | Não |

**Response `201`:** Objeto `AccessLink` criado.

---

### `PATCH /acessos/{acessoId}`

Atualiza um link. **Response `200`.**

---

### `DELETE /acessos/{acessoId}`

Exclui um link. **Response `204`.**

---

## FAQ / Inclusões

### `GET /faq`

Lista itens de FAQ.

**Response `200`:**
```json
[
  {
    "id": 1,
    "data": "2026-01-20",
    "usuario": "João Silva",
    "setor": "Fiscal",
    "consultoria": "Consultoria ABC",
    "assunto": "Como emitir NF-e?",
    "pergunta": "Qual o procedimento para emissão de NF-e?",
    "resposta": "Acessar o portal SEFAZ e..."
  }
]
```

---

### `GET /faq/{faqId}`

Retorna detalhes de um item de FAQ.

---

### `POST /faq`

Cria um novo item de FAQ.

**Request Body:**
```json
{
  "data": "2026-02-13",
  "usuario": "Maria Santos",
  "setor": "Contábil",
  "consultoria": "Interna",
  "assunto": "Lançamento contábil",
  "pergunta": "Como fazer lançamento de provisão?",
  "resposta": "Utilizar a conta 2.1.01..."
}
```

**Response `201`:** Objeto `FAQ` criado.

---

### `PATCH /faq/{faqId}`

Atualiza um item de FAQ. **Response `200`.**

---

### `DELETE /faq/{faqId}`

Exclui um item de FAQ. **Response `204`.**

---

## Diretoria - Eventos

### `GET /diretoria/eventos`

Lista eventos cadastrados pela diretoria.

**Response `200`:**
```json
[
  {
    "id": 1,
    "name": "Confraternização de Fim de Ano",
    "event_type": "Confraternização",
    "event_date": "2025-12-20",
    "description": "Evento anual de confraternização",
    "audience": "Todos os colaboradores",
    "participants": "50 pessoas",
    "services": "Buffet, DJ, Decoração",
    "total_cost": 5000.00,
    "photos": null,
    "created_by_id": 1,
    "created_at": "2025-11-15T10:00:00",
    "updated_at": null
  }
]
```

---

### `GET /diretoria/eventos/{eventoId}`

Retorna detalhes de um evento.

---

### `POST /diretoria/eventos`

Cria um novo evento da diretoria.

**Request Body:**
```json
{
  "name": "Evento Corporativo",
  "event_type": "Workshop",
  "event_date": "2026-05-10",
  "description": "Workshop de liderança",
  "audience": "Gestores",
  "participants": "15 pessoas",
  "services": "Coffee break, Material",
  "total_cost": 2000.00
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `name` | string | Sim |
| Demais campos | string/number | Não |

**Response `201`:** Objeto `DiretoriaEvent` criado.

---

### `PATCH /diretoria/eventos/{eventoId}`

Atualiza um evento. **Response `200`.**

---

### `DELETE /diretoria/eventos/{eventoId}`

Exclui um evento. **Response `204`.**

---

## Diretoria - Acordos

### `GET /diretoria/acordos`

Lista acordos da diretoria.

**Response `200`:**
```json
[
  {
    "id": 1,
    "user_id": 1,
    "title": "Acordo de participação nos lucros",
    "agreement_date": "2026-01-10",
    "description": "Detalhes do acordo...",
    "notes": "Detalhes do acordo...",
    "status": null,
    "created_at": "2026-01-10T14:00:00",
    "updated_at": null
  }
]
```

---

### `GET /diretoria/acordos/{acordoId}`

Retorna detalhes de um acordo.

---

### `POST /diretoria/acordos`

Cria um novo acordo.

**Request Body:**
```json
{
  "title": "Novo acordo",
  "agreement_date": "2026-03-01",
  "description": "Detalhes do acordo"
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `title` | string | Sim |
| `agreement_date` | string (`YYYY-MM-DD`) | Não |
| `description` | string | Não |

**Response `201`:** Objeto `DiretoriaAgreement` criado.

---

### `PATCH /diretoria/acordos/{acordoId}`

Atualiza um acordo. **Response `200`.**

---

### `DELETE /diretoria/acordos/{acordoId}`

Exclui um acordo. **Response `204`.**

---

## Diretoria - Feedbacks

### `GET /diretoria/feedbacks`

Lista feedbacks da diretoria.

**Response `200`:**
```json
[
  {
    "id": 1,
    "user_id": 1,
    "title": "Feedback sobre desempenho",
    "feedback_type": "Feedback sobre desempenho",
    "description": "Avaliação trimestral...",
    "content": "Avaliação trimestral...",
    "feedback_date": "2026-01-30",
    "created_at": "2026-01-30T16:00:00",
    "updated_at": null
  }
]
```

---

### `GET /diretoria/feedbacks/{feedbackId}`

Retorna detalhes de um feedback.

---

### `POST /diretoria/feedbacks`

Cria um novo feedback.

**Request Body:**
```json
{
  "title": "Feedback mensal",
  "description": "Avaliação do mês de fevereiro",
  "feedback_date": "2026-02-28"
}
```

| Campo | Tipo | Obrigatório |
|-------|------|:-----------:|
| `title` | string | Sim |
| `description` | string | Não |
| `feedback_date` | string (`YYYY-MM-DD`) | Não |

**Response `201`:** Objeto `DiretoriaFeedback` criado.

---

## Permissões de Relatórios

> Todos os endpoints deste módulo requerem **permissão de admin**.

### `GET /reports/permissions`

Lista permissões de acesso a relatórios.

**Response `200`:**
```json
[
  {
    "id": 1,
    "report_code": "relatorio_fiscal",
    "tag_id": 1,
    "user_id": null,
    "created_at": "2026-01-01T10:00:00",
    "updated_at": null
  }
]
```

---

### `POST /reports/permissions`

Concede acesso a um relatório para uma tag ou usuário.

**Request Body:**
```json
{
  "report_code": "relatorio_empresas",
  "tag_id": 2,
  "user_id": null
}
```

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|:-----------:|-----------|
| `report_code` | string | Sim | Código do relatório |
| `tag_id` | integer | Não | ID da tag (acesso por setor) |
| `user_id` | integer | Não | ID do usuário (acesso individual) |

**Response `201`:** Objeto `ReportPermission` criado.

---

### `DELETE /reports/permissions/{permissionId}`

Revoga acesso a um relatório. **Response `204`.**

---

## Códigos de Erro

### Erros de Autenticação

| Código | Descrição |
|--------|-----------|
| `missing_token` | Header `Authorization` ausente |
| `invalid_token` | Token com assinatura inválida |
| `token_expired` | Token expirado (TTL: 24h) |
| `user_not_found_or_inactive` | Usuário não existe ou está inativo |
| `invalid_credentials` | Username/email ou senha incorretos |
| `inactive_user` | Conta de usuário desativada |

### Erros de Autorização

| Código | Descrição |
|--------|-----------|
| `forbidden` | Sem permissão para esta ação |
| `forbidden_for_tag` | Sem acesso à tag especificada |

### Erros de Validação

| Código | Descrição |
|--------|-----------|
| `title_and_tag_id_required` | Campos obrigatórios ausentes na criação de tarefa |
| `title_cannot_be_empty` | Título não pode ser vazio |
| `body_required` | Corpo do comentário obrigatório |
| `user_id_required` | ID do usuário obrigatório |
| `file_required` | Arquivo obrigatório no upload |
| `invalid_status` | Valor de status inválido |
| `invalid_priority` | Valor de prioridade inválido |
| `invalid_due_date` | Formato de data inválido (esperado: `YYYY-MM-DD`) |
| `invalid_date` | Data inválida |
| `invalid_data_abertura` | Data de abertura inválida |
| `invalid_fields` | Campos solicitados não existem |
| `subject_content_date_required` | Campos obrigatórios do comunicado ausentes |
| `nome_cnpj_data_abertura_codigo_required` | Campos obrigatórios da empresa ausentes |

### Erros de Recurso

| Código | Descrição |
|--------|-----------|
| `not_found` | Recurso não encontrado |
| `tag_not_found` | Tag não encontrada |
| `assignee_not_found` | Usuário atribuído não encontrado |
| `user_not_found` | Usuário não encontrado |

### Erros de Servidor

| Código | Descrição |
|--------|-----------|
| `failed_to_create_task` | Falha ao criar tarefa |
| `failed_to_update_task` | Falha ao atualizar tarefa |
| `failed_to_delete_task` | Falha ao excluir tarefa |
| `failed_to_create_comment` | Falha ao criar comentário |
| `failed_to_add_follower` | Falha ao adicionar seguidor |
| `failed_to_remove_follower` | Falha ao remover seguidor |
| `failed_to_upload` | Falha no upload de arquivo |
| `failed_to_create` | Falha ao criar recurso |
| `failed_to_update` | Falha ao atualizar recurso |
| `failed_to_delete` | Falha ao excluir recurso |
| `failed_to_mark_read` | Falha ao marcar como lido |
