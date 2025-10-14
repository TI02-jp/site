-- Script SQL para adicionar colunas de recorrência na tabela reunioes
-- Execute este script manualmente no MySQL se o script Python falhar

-- Adicionar coluna recorrencia_tipo se não existir
ALTER TABLE reunioes
ADD COLUMN IF NOT EXISTS recorrencia_tipo ENUM('NENHUMA', 'DIARIA', 'SEMANAL', 'QUINZENAL', 'MENSAL', 'ANUAL')
NOT NULL DEFAULT 'NENHUMA';

-- Adicionar coluna recorrencia_fim se não existir
ALTER TABLE reunioes
ADD COLUMN IF NOT EXISTS recorrencia_fim DATE NULL;

-- Adicionar coluna recorrencia_grupo_id se não existir
ALTER TABLE reunioes
ADD COLUMN IF NOT EXISTS recorrencia_grupo_id VARCHAR(36) NULL;

-- Adicionar coluna recorrencia_dias_semana se não existir
ALTER TABLE reunioes
ADD COLUMN IF NOT EXISTS recorrencia_dias_semana VARCHAR(20) NULL;

-- Atualizar versão do Alembic
UPDATE alembic_version
SET version_num = 'add_meeting_recurrence';

-- Verificar colunas adicionadas
DESCRIBE reunioes;
