Kit de Diagnóstico
==================

Instrumentações de performance foram conectadas à aplicação Flask para explicar travamentos de requisições. Este guia mostra como habilitar cada camada e interpretar os dados coletados.

Variáveis de Configuração
-------------------------

- `SLOW_REQUEST_THRESHOLD_MS` (padrão: `750`): duração mínima para promover uma requisição a um log estruturado com detalhamento de SQL, chamadas externas, templates e commits.
- `ENABLE_DIAGNOSTICS=1`: expõe o endpoint protegido `/_diagnostics/thread-state`, que captura um instantâneo das threads ativas do Waitress.
- `DIAGNOSTICS_TOKEN`: segredo opcional exigido no header `X-Diagnostics-Token` para acessar os diagnósticos.
- `WAITRESS_LOG_LEVEL` (padrão: `info`): nível de log encaminhado ao Waitress via `run.py`.

Middleware de Performance
-------------------------

O middleware grava entradas `SLOW REQUEST: {…}` em `logs/app.log`. Cada payload inclui:

- `duration_ms`, `sql_time_ms` e contadores de SQL, chamadas externas, templates e commits.
- Métricas por query (texto SQL truncado, tempo de execução).
- Duração de chamadas externas (ex.: APIs Google).
- Tempo de renderização de templates e commits.

Requisições normais são registradas em nível `DEBUG` (`REQUEST PERF`) para evitar ruído em produção. Aumente a verbosidade se precisar inspecionar todas as requisições (`export FLASK_ENV=development` ou ajuste o nível do logger manualmente).

Wrappers de Chamadas Externas
-----------------------------

`app/utils/google_api_monitor.py` disponibiliza:

- `instrumented_request` para chamadas HTTP com cronometragem, timeouts padrão e logging de status.
- `monitor_google_call` para envolver objetos de serviço e execuções em lote.

Envolva endpoints lentos (Google Calendar ou similares) para obter visibilidade imediata das dependências externas.

Tempo de SQL e Commits
----------------------

O middleware conecta eventos de cursor do SQLAlchemy para medir cada query. Commits executados nos handlers de `teardown_appcontext` em `app/__init__.py` também são cronometrados, evidenciando lock waits no payload.

Diagnóstico de Threads
----------------------

Com `ENABLE_DIAGNOSTICS=1`, a aplicação oferece `/_diagnostics/thread-state`. O endpoint informa:

- Total de threads do processo e quantas aparentam ser workers do Waitress.
- Valor configurado de `WAITRESS_THREADS`.

Use o script auxiliar:

```powershell
python scripts/monitor_waitress_threads.py --token "%DIAGNOSTICS_TOKEN%"
```

Ele consulta o endpoint continuamente e imprime um resumo compacto para acompanhamento durante incidentes.

Análise de Logs
---------------

Para inspecionar requisições lentas históricas:

```powershell
python scripts/analyze_slow_requests.py --log logs/app.log
```

O script agrega por rota, mostrando os endpoints mais pesados e as instruções SQL mais recorrentes entre os eventos lentos.

Próximos Passos
---------------

1. Ative o slow query log no banco (`long_query_time=1`) para complementar as métricas do middleware.
2. Envolva integrações externas críticas (Google Calendar, Drive, SSE) com `monitor_google_call`.
3. Execute o monitor de threads sob carga controlada, compare `waitress_thread_count` com o limite configurado e registre stack traces com `faulthandler` quando uma requisição ultrapassar 10 segundos.
