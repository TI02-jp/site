API v1 – Guia Rápido
====================

Base: `/api/v1`. Header padrão: `Authorization: Bearer <token>` (exceto login). JSON por padrão; uploads em multipart.

Autenticação
------------
- Login: `POST /auth/login` → `{ token, expires_in, user }`
  - Body: `{ "username"|"email": "...", "password": "..." }`
- Refresh: `POST /auth/refresh`
- Quem sou: `GET /me`

Catálogos
---------
- Tags: `GET /tags`
- Usuários: `GET /users`

Tarefas
-------
- Listar: `GET /tasks?status=pending|in_progress|done`
- Detalhe: `GET /tasks/{id}`
- Criar: `POST /tasks` body mínimo `{ "title": "...", "tag_id": 1 }`
- Editar: `PATCH /tasks/{id}` (`title|description|status|priority|due_date`)
- Status rápido: `POST /tasks/{id}/status` `{ "status": "done" }`
- Deletar: `DELETE /tasks/{id}` (admin ou criador/atribuído)
- Comentários: `GET/POST /tasks/{id}/comments` body `{ "body": "texto" }`
- Anexar arquivo: `POST /tasks/{id}/attachments` multipart campo `file`
- Seguidores: `GET/POST /tasks/{id}/followers` body `{ "user_id": 123 }`; `DELETE /tasks/{id}/followers/{user_id}`
- Histórico: `GET /tasks/{id}/history`

Notificações
------------
- Listar: `GET /notifications?limit=50`
- Marcar lida(s): `POST /notifications/read` (com ou sem `notification_id`)

Anúncios
--------
- Listar/detalhe: `GET /announcements?limit=`, `GET /announcements/{id}`
- Criar (admin): `POST /announcements` JSON `subject|content|date(YYYY-MM-DD)` + opcional multipart `attachment`
- Editar (admin): `PATCH /announcements/{id}` (aceita `attachment`)
- Deletar (admin): `DELETE /announcements/{id}`
- Marcar lido: `POST /announcements/{id}/read`

Agenda / Calendários
--------------------
- Reuniões (Google): `GET /reunioes`
- Eventos internos: `GET /calendario-eventos`

Empresas / Departamentos (admin)
--------------------------------
- Empresas: `GET /empresas`, `GET /empresas/{id}`, `POST /empresas`, `PATCH /empresas/{id}`, `DELETE /empresas/{id}`
  - Campos obrigatórios no POST: `nome/nome_empresa`, `cnpj`, `data_abertura`, `codigo_empresa`
- Departamentos: `GET /empresas/{id}/departamentos`, `POST /departamentos`, `PATCH /departamentos/{id}`, `DELETE /departamentos/{id}`

Procedimentos
-------------
- Listar/detalhe: `GET /procedimentos`, `GET /procedimentos/{id}`
- CRUD (admin): `POST /procedimentos`, `PATCH /procedimentos/{id}`, `DELETE /procedimentos/{id}`

Notas (admin)
-------------
- Débito: `GET /notas/debito`, `GET /notas/debito/{id}`, `POST /notas/debito`, `PATCH /notas/debito/{id}`, `DELETE /notas/debito/{id}`
- Cadastro: `GET /notas/cadastro`, `GET /notas/cadastro/{id}`, `POST /notas/cadastro`, `PATCH /notas/cadastro/{id}`, `DELETE /notas/cadastro/{id}`
- Recorrentes: `GET /notas/recorrentes`, `GET /notas/recorrentes/{id}`, `POST /notas/recorrentes`, `PATCH /notas/recorrentes/{id}`, `DELETE /notas/recorrentes/{id}`

Cursos (admin)
--------------
- Cursos: `GET /courses`, `GET /courses/{id}`, `POST /courses`, `PATCH /courses/{id}`, `DELETE /courses/{id}`
- Tags de curso: `GET /course-tags`, `POST /course-tags`, `DELETE /course-tags/{id}`

Acessos (admin)
---------------
- Links: `GET /acessos`, `POST /acessos`, `PATCH /acessos/{id}`, `DELETE /acessos/{id}`

FAQ
---
- `GET /faq`, `GET /faq/{id}`, `POST /faq` (admin), `PATCH /faq/{id}` (admin), `DELETE /faq/{id}` (admin)

Diretoria
---------
- Eventos: `GET /diretoria/eventos`, `GET /diretoria/eventos/{id}`, `POST /diretoria/eventos` (admin), `PATCH /diretoria/eventos/{id}` (admin), `DELETE /diretoria/eventos/{id}` (admin)
- Acordos: `GET /diretoria/acordos`, `GET /diretoria/acordos/{id}`, `POST /diretoria/acordos` (admin), `PATCH /diretoria/acordos/{id}` (admin), `DELETE /diretoria/acordos/{id}` (admin)
- Feedbacks: `GET /diretoria/feedbacks`, `GET /diretoria/feedbacks/{id}`, `POST /diretoria/feedbacks` (autenticado)

Report Permissions (admin)
--------------------------
- `GET /reports/permissions`, `POST /reports/permissions`, `DELETE /reports/permissions/{id}`

Uploads
-------
- Tarefas: `POST /tasks/{id}/attachments` — multipart campo `file`
- Anúncios: `POST/PATCH /announcements` — multipart campo `attachment` (opcional)

Erros (padrão)
--------------
- 400 validação (`invalid_*`, `*_required`)
- 401 token (`missing_token`, `token_expired`, `invalid_token`)
- 403 `forbidden`
- 404 `not_found`
- 500 `failed_to_*`
