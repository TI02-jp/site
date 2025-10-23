Diagnostics Toolkit
===================

Performance instrumentation has been wired into the Flask app to help explain request stalls. This guide shows how to enable each layer and read the collected data.

Configuration Flags
-------------------

- `SLOW_REQUEST_THRESHOLD_MS` (default: `750`): minimum duration that promotes a request to a structured log entry with SQL, external call, template, and commit breakdowns.
- `ENABLE_DIAGNOSTICS=1`: exposes the protected endpoint `/_diagnostics/thread-state` that snapshots live Waitress threads.
- `DIAGNOSTICS_TOKEN`: optional shared secret required in the `X-Diagnostics-Token` header for diagnostics access.
- `WAITRESS_LOG_LEVEL` (default: `info`): forwarded to the Waitress logger from `run.py`.

Performance Middleware
----------------------

The middleware emits `SLOW REQUEST: {…}` entries to `logs/app.log`. Each payload includes:

- `duration_ms`, `sql_time_ms`, and counts for SQL, external calls, templates, and commits.
- Per-query metrics (truncated SQL text, execution time).
- External call timings (e.g., Google APIs).
- Template render durations and commit timing.

Normal requests are logged at `DEBUG` level (`REQUEST PERF`) to avoid polluting production logs. Increase log verbosity if you need every request (`export FLASK_ENV=development` or set the logger level manually).

External Call Wrappers
----------------------

`app/utils/google_api_monitor.py` exposes:

- `instrumented_request` for HTTP calls with timing, default timeouts, and status logging.
- `monitor_google_call` helpers for service objects and batch execution.

Wrap Google Calendar (or other slow) endpoints to get immediate visibility into slow external dependencies.

SQL & Commit Timing
-------------------

The middleware hooks SQLAlchemy cursor events to time every query. Commits executed in `app/__init__.py` teardown handlers are wrapped so lock waits surface in the payload.

Thread Diagnostics
------------------

When `ENABLE_DIAGNOSTICS=1`, the application serves `/_diagnostics/thread-state`. It reports:

- Total threads in the process plus those whose names indicate Waitress workers.
- Configured `WAITRESS_THREADS`.

Use the helper script:

```powershell
python scripts/monitor_waitress_threads.py --token "%DIAGNOSTICS_TOKEN%"
```

It polls the endpoint and prints a compact summary that you can watch during incidents.

Log Analysis
------------

Parse historical slow requests with:

```powershell
python scripts/analyze_slow_requests.py --log logs/app.log
```

The script aggregates by route, printing the heaviest endpoints and quoting the most common slow SQL fragments.

Next Steps
----------

1. Enable slow query logging on the database (`long_query_time=1`) to augment the middleware metrics.
2. Wrap high-risk external integrations (Google Calendar, Drive, SSE endpoints) with `monitor_google_call`.
3. Run the thread monitor during simulated load, compare `waitress_thread_count` against the configured limit, and record stack traces with `faulthandler` when a request surpasses 10 seconds.
