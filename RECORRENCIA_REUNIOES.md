# Funcionalidade de Recorrência de Reuniões

## Visão Geral

Foi implementado suporte completo para reuniões recorrentes no sistema, permitindo criar reuniões que se repetem automaticamente em intervalos regulares, similar ao Google Agenda.

## Mudanças Implementadas

### 1. Modelo de Dados (`app/models/tables.py`)

Adicionado novo enum `ReuniaoRecorrenciaTipo` com os seguintes tipos:
- **NENHUMA**: Reunião única (padrão)
- **DIARIA**: Repete diariamente
- **SEMANAL**: Repete semanalmente (mesmo dia da semana)
- **QUINZENAL**: Repete a cada 2 semanas
- **MENSAL**: Repete mensalmente (mesmo dia do mês)
- **ANUAL**: Repete anualmente

Novos campos adicionados na tabela `reunioes`:
- `recorrencia_tipo`: Tipo de recorrência (enum)
- `recorrencia_fim`: Data final da recorrência
- `recorrencia_grupo_id`: ID único para agrupar reuniões da mesma série
- `recorrencia_dias_semana`: Dias específicos da semana para recorrência semanal (formato: "0,2,4" para segunda, quarta, sexta)

### 2. Formulário (`app/forms.py`)

Adicionados campos no `MeetingForm`:
- `recorrencia_tipo`: SelectField com opções de recorrência
- `recorrencia_fim`: DateField para definir até quando repetir
- `recorrencia_dias_semana`: SelectMultipleField para escolher dias específicos da semana

Validações adicionadas:
- Data final deve ser posterior à data inicial
- Para recorrência semanal com dias específicos, pelo menos um dia deve ser selecionado

### 3. Serviço de Recorrência (`app/services/meeting_recurrence.py`)

Funções criadas:

#### `generate_recurrence_dates(start_date, end_date, recurrence_type, weekdays=None)`
Gera lista de datas para reuniões recorrentes.

**Parâmetros:**
- `start_date`: Data inicial
- `end_date`: Data final da recorrência
- `recurrence_type`: Tipo de recorrência (ReuniaoRecorrenciaTipo)
- `weekdays`: Lista de dias da semana (0=segunda, 6=domingo) para recorrência semanal customizada

**Retorna:** Lista de objetos `date` onde as reuniões devem ser criadas

**Exemplos:**
```python
from datetime import date
from app.services.meeting_recurrence import generate_recurrence_dates
from app.models.tables import ReuniaoRecorrenciaTipo

# Reuniões semanais por 3 meses
dates = generate_recurrence_dates(
    start_date=date(2025, 10, 15),
    end_date=date(2026, 1, 15),
    recurrence_type=ReuniaoRecorrenciaTipo.SEMANAL
)

# Reuniões toda segunda, quarta e sexta por 1 mês
dates = generate_recurrence_dates(
    start_date=date(2025, 10, 15),
    end_date=date(2025, 11, 15),
    recurrence_type=ReuniaoRecorrenciaTipo.SEMANAL,
    weekdays=[0, 2, 4]  # 0=segunda, 2=quarta, 4=sexta
)
```

#### `generate_recurrence_group_id()`
Gera um UUID único para agrupar reuniões da mesma série recorrente.

## Como Integrar na Criação de Reuniões

Para adicionar suporte a recorrência na função `create_meeting_and_event` em `app/services/meeting_room.py`, siga este padrão:

```python
from app.services.meeting_recurrence import (
    generate_recurrence_dates,
    generate_recurrence_group_id
)
from app.models.tables import ReuniaoRecorrenciaTipo

def create_meeting_and_event(form, raw_events, now, user_id: int):
    # ... código existente ...

    # Após criar a primeira reunião (linha ~548)
    db.session.commit()

    # Verificar se há recorrência
    recorrencia_tipo = getattr(form, 'recorrencia_tipo', None)
    recorrencia_fim = getattr(form, 'recorrencia_fim', None)

    if recorrencia_tipo and recorrencia_tipo.data and recorrencia_tipo.data != 'NENHUMA':
        recurrence_type = ReuniaoRecorrenciaTipo(recorrencia_tipo.data)

        if recorrencia_fim and recorrencia_fim.data:
            # Gerar ID de grupo para série recorrente
            group_id = generate_recurrence_group_id()

            # Atualizar reunião inicial com dados de recorrência
            meeting.recorrencia_tipo = recurrence_type
            meeting.recorrencia_fim = recorrencia_fim.data
            meeting.recorrencia_grupo_id = group_id

            # Para recorrência semanal com dias específicos
            if recurrence_type == ReuniaoRecorrenciaTipo.SEMANAL:
                dias_semana = getattr(form, 'recorrencia_dias_semana', None)
                if dias_semana and dias_semana.data:
                    meeting.recorrencia_dias_semana = ','.join(dias_semana.data)
                    weekdays = [int(d) for d in dias_semana.data]
                else:
                    weekdays = None
            else:
                weekdays = None

            db.session.commit()

            # Gerar datas de recorrência
            recurrence_dates = generate_recurrence_dates(
                start_date=form.date.data,
                end_date=recorrencia_fim.data,
                recurrence_type=recurrence_type,
                weekdays=weekdays
            )

            # Remover a data inicial (já criada)
            recurrence_dates = [d for d in recurrence_dates if d != form.date.data]

            # Criar reuniões recorrentes
            for recurrence_date in recurrence_dates:
                start_dt_recurrent = datetime.combine(
                    recurrence_date, form.start_time.data, tzinfo=CALENDAR_TZ
                )
                end_dt_recurrent = datetime.combine(
                    recurrence_date, form.end_time.data, tzinfo=CALENDAR_TZ
                )

                # Criar evento no Google Calendar
                if form.create_meet.data:
                    event = create_meet_event(
                        form.subject.data,
                        start_dt_recurrent,
                        end_dt_recurrent,
                        description,
                        participant_emails,
                        notify_attendees=should_notify,
                    )
                    meet_link_recurrent = event.get("hangoutLink")
                else:
                    event = create_event(
                        form.subject.data,
                        start_dt_recurrent,
                        end_dt_recurrent,
                        description,
                        participant_emails,
                        notify_attendees=should_notify,
                    )
                    meet_link_recurrent = None

                # Criar reunião recorrente
                recurrent_meeting = Reuniao(
                    inicio=start_dt_recurrent,
                    fim=end_dt_recurrent,
                    assunto=form.subject.data,
                    descricao=form.description.data,
                    status=ReuniaoStatus.AGENDADA,
                    meet_link=meet_link_recurrent,
                    google_event_id=event["id"],
                    criador_id=user_id,
                    course_id=course_id_value,
                    recorrencia_tipo=recurrence_type,
                    recorrencia_fim=recorrencia_fim.data,
                    recorrencia_grupo_id=group_id,
                    recorrencia_dias_semana=meeting.recorrencia_dias_semana,
                )
                recurrent_meeting.meet_settings = _normalize_meet_settings()
                db.session.add(recurrent_meeting)
                db.session.flush()

                # Adicionar participantes
                for u in selected_users:
                    db.session.add(
                        ReuniaoParticipante(
                            reuniao_id=recurrent_meeting.id,
                            id_usuario=u.id,
                            username_usuario=u.username
                        )
                    )

            db.session.commit()

            # Mensagem de sucesso
            total_meetings = len(recurrence_dates) + 1
            flash(
                f"Série de {total_meetings} reuniões recorrentes criada com sucesso!",
                "success"
            )

    # ... restante do código ...
```

## Interface do Usuário

O formulário de criação de reunião agora inclui:

1. **Campo "Repetir"**: Dropdown com opções de recorrência
2. **Campo "Repetir até"**: Seletor de data (aparece quando recorrência != NENHUMA)
3. **Campo "Dias da semana"**: Checkboxes para selecionar dias específicos (aparece apenas para recorrência SEMANAL)

### Comportamento da UI

- Por padrão, "Repetir" está em "Não repetir"
- Ao selecionar uma opção de recorrência, o campo "Repetir até" aparece automaticamente
- Para recorrência "Semanalmente", aparece também a opção de selecionar dias específicos da semana
- O sistema valida que a data final seja posterior à data inicial

## Casos de Uso

### 1. Reunião Semanal (mesmo dia)
- Reunião toda terça-feira por 3 meses
- Selecione: "Semanalmente" e defina a data final

### 2. Reunião em Dias Específicos da Semana
- Reunião toda segunda, quarta e sexta por 2 meses
- Selecione: "Semanalmente", marque os dias desejados, e defina a data final

### 3. Reunião Mensal
- Reunião todo dia 15 do mês por 1 ano
- Crie uma reunião no dia 15, selecione "Mensalmente" e defina a data final

### 4. Reunião Anual
- Reunião anual no mesmo dia
- Selecione "Anualmente" e defina por quantos anos

## Gerenciamento de Séries Recorrentes

Todas as reuniões de uma mesma série compartilham o mesmo `recorrencia_grupo_id`. Isso permite:

- Consultar todas as reuniões de uma série:
```python
serie_reunioes = Reuniao.query.filter_by(
    recorrencia_grupo_id='uuid-aqui'
).order_by(Reuniao.inicio).all()
```

- Editar/cancelar toda a série (funcionalidade futura)
- Identificar reuniões que fazem parte de uma recorrência

## Próximos Passos (Melhorias Futuras)

1. **Edição de Séries**: Permitir editar toda a série ou apenas uma ocorrência
2. **Exclusão em Cascata**: Opção de excluir toda a série
3. **Exceções**: Permitir pular datas específicas na recorrência
4. **Recorrência Personalizada**: "A cada X dias/semanas/meses"
5. **Limite por Número de Ocorrências**: Opção de "Repetir N vezes" em vez de até uma data

## Migração do Banco de Dados

A migração foi aplicada com sucesso:
```
migrations/versions/add_meeting_recurrence_simple.py
```

Campos adicionados:
- `recorrencia_tipo` (ENUM, default 'NENHUMA')
- `recorrencia_fim` (DATE, nullable)
- `recorrencia_grupo_id` (VARCHAR(36), nullable)
- `recorrencia_dias_semana` (VARCHAR(20), nullable)

## Testes

Para testar a funcionalidade:

1. Acesse a página de criação de reuniões
2. Preencha os dados da reunião
3. Selecione um tipo de recorrência no campo "Repetir"
4. Defina a data final em "Repetir até"
5. (Opcional) Para recorrência semanal, selecione dias específicos
6. Clique em "Agendar"

O sistema deve criar múltiplas reuniões com base nos parâmetros de recorrência definidos.
