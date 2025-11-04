-- Script de Verificacao do Banco de Dados - Tarefas "Somente para Mim"
-- Execute este script para diagnosticar problemas com tarefas privadas

-- ======================================================================
-- 1. VERIFICAR ESTRUTURA DA TABELA TASKS
-- ======================================================================
-- Descricao: Verifica se a coluna is_private existe na tabela
SELECT
    COLUMN_NAME,
    COLUMN_TYPE,
    IS_NULLABLE,
    COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'tasks'
    AND TABLE_SCHEMA = DATABASE()
    AND COLUMN_NAME IN ('id', 'title', 'is_private', 'tag_id', 'created_by', 'assigned_to', 'created_at');

-- ======================================================================
-- 2. CONTAR TODAS AS TAREFAS POR STATUS DE PRIVACIDADE
-- ======================================================================
SELECT
    is_private,
    COUNT(*) as total_tarefas
FROM tasks
GROUP BY is_private;

-- ======================================================================
-- 3. LISTAR ULTIMAS 20 TAREFAS PRIVADAS CRIADAS
-- ======================================================================
SELECT
    t.id,
    t.title,
    t.is_private,
    t.status,
    t.tag_id,
    tag.nome as tag_nome,
    t.created_by,
    u_creator.username as criado_por,
    t.assigned_to,
    u_assigned.username as atribuido_para,
    t.created_at,
    t.updated_at
FROM tasks t
LEFT JOIN tags tag ON t.tag_id = tag.id
LEFT JOIN users u_creator ON t.created_by = u_creator.id
LEFT JOIN users u_assigned ON t.assigned_to = u_assigned.id
WHERE t.is_private = 1
ORDER BY t.created_at DESC
LIMIT 20;

-- ======================================================================
-- 4. VERIFICAR TAGS PESSOAIS
-- ======================================================================
-- Descricao: Tags pessoais devem ter nome no formato __personal__<user_id>
SELECT
    id,
    nome,
    user_id
FROM tags
WHERE nome LIKE '__personal__%'
ORDER BY id DESC;

-- ======================================================================
-- 5. TAREFAS PRIVADAS SEM TAG PESSOAL (POTENCIAL PROBLEMA)
-- ======================================================================
-- Descricao: Tarefas marcadas como privadas devem usar tags pessoais
SELECT
    t.id,
    t.title,
    t.is_private,
    t.tag_id,
    tag.nome as tag_nome,
    CASE
        WHEN tag.nome LIKE '__personal__%' THEN 'OK'
        ELSE 'PROBLEMA: tag nao e pessoal'
    END as status_tag
FROM tasks t
LEFT JOIN tags tag ON t.tag_id = tag.id
WHERE t.is_private = 1
    AND (tag.nome NOT LIKE '__personal__%' OR tag.nome IS NULL)
ORDER BY t.created_at DESC
LIMIT 20;

-- ======================================================================
-- 6. VERIFICAR ULTIMAS TAREFAS CRIADAS (INCLUINDO NAO-PRIVADAS)
-- ======================================================================
-- Descricao: Ver todas as tarefas recentes para comparacao
SELECT
    t.id,
    t.title,
    t.is_private,
    t.status,
    tag.nome as tag_nome,
    u_creator.username as criado_por,
    u_assigned.username as atribuido_para,
    t.created_at
FROM tasks t
LEFT JOIN tags tag ON t.tag_id = tag.id
LEFT JOIN users u_creator ON t.created_by = u_creator.id
LEFT JOIN users u_assigned ON t.assigned_to = u_assigned.id
ORDER BY t.created_at DESC
LIMIT 30;

-- ======================================================================
-- 7. HISTORICO DE MUDANCAS NO CAMPO is_private
-- ======================================================================
-- Descricao: Ver se houve alteracoes no campo is_private
SELECT
    th.task_id,
    t.title,
    th.field_name,
    th.old_value,
    th.new_value,
    th.changed_by,
    u.username as alterado_por,
    th.changed_at
FROM task_history th
LEFT JOIN tasks t ON th.task_id = t.id
LEFT JOIN users u ON th.changed_by = u.id
WHERE th.field_name = 'is_private'
ORDER BY th.changed_at DESC
LIMIT 20;

-- ======================================================================
-- 8. TAREFAS PRIVADAS POR USUARIO
-- ======================================================================
-- Descricao: Quantidade de tarefas privadas por usuario criador
SELECT
    u.id as user_id,
    u.username,
    u.name,
    COUNT(t.id) as total_tarefas_privadas
FROM users u
LEFT JOIN tasks t ON u.id = t.created_by AND t.is_private = 1
GROUP BY u.id, u.username, u.name
HAVING total_tarefas_privadas > 0
ORDER BY total_tarefas_privadas DESC;

-- ======================================================================
-- 9. VERIFICAR INTEGRIDADE: TAREFAS PRIVADAS X ASSIGNED_TO
-- ======================================================================
-- Descricao: Tarefas privadas devem ter assigned_to igual ao created_by
SELECT
    t.id,
    t.title,
    t.is_private,
    t.created_by,
    u_creator.username as criado_por,
    t.assigned_to,
    u_assigned.username as atribuido_para,
    CASE
        WHEN t.created_by = t.assigned_to THEN 'OK'
        ELSE 'AVISO: criador diferente do atribuido'
    END as status_atribuicao
FROM tasks t
LEFT JOIN users u_creator ON t.created_by = u_creator.id
LEFT JOIN users u_assigned ON t.assigned_to = u_assigned.id
WHERE t.is_private = 1
ORDER BY t.created_at DESC
LIMIT 20;

-- ======================================================================
-- 10. VERIFICAR SE HA TAREFAS COM is_private = NULL
-- ======================================================================
-- Descricao: O campo is_private nao deve ser NULL
SELECT
    COUNT(*) as tarefas_com_is_private_null
FROM tasks
WHERE is_private IS NULL;

-- ======================================================================
-- FIM DO SCRIPT DE VERIFICACAO
-- ======================================================================
--
-- INSTRUCOES DE USO:
-- 1. Abra seu cliente MySQL/MariaDB
-- 2. Selecione o banco de dados da aplicacao
-- 3. Execute este script completo
-- 4. Analise os resultados de cada consulta
-- 5. Procure por:
--    - Tarefas privadas sem tag pessoal (query 5)
--    - Tarefas com is_private NULL (query 10)
--    - Discrepancias entre created_by e assigned_to (query 9)
--    - Falta de tarefas privadas quando esperadas (queries 2, 3, 6)
