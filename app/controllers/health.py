"""Health check endpoints for monitoring and load balancers."""

from flask import jsonify
from sqlalchemy import text
from datetime import datetime
from app import app, db, limiter


@app.route("/health")
@limiter.exempt  # Don't rate limit health checks
def health_check():
    """Health check endpoint for monitoring and load balancers.

    Returns:
        JSON response with health status and metrics
        - 200: Healthy
        - 503: Unhealthy (database connection failed or critical issues)
    """
    import psutil
    from threading import active_count

    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {}
    }

    # 1. Database connectivity check
    try:
        db.session.execute(text("SELECT 1"))
        db.session.commit()
        health_status["checks"]["database"] = {
            "status": "ok",
            "message": "Database connection successful"
        }
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["checks"]["database"] = {
            "status": "error",
            "message": f"Database connection failed: {str(e)}"
        }
        return jsonify(health_status), 503

    # 2. Memory check (warn if >85% used)
    try:
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        health_status["checks"]["memory"] = {
            "status": "ok" if memory_percent < 85 else "warning",
            "usage_percent": round(memory_percent, 2),
            "available_mb": round(memory.available / 1024 / 1024, 2)
        }
        if memory_percent >= 90:
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["checks"]["memory"] = {
            "status": "unknown",
            "message": f"Could not check memory: {str(e)}"
        }

    # 3. Active threads check
    try:
        thread_count = active_count()
        # Warn if more than 80% of Waitress threads are in use (assuming 16 threads)
        max_threads = 16
        thread_usage_percent = (thread_count / max_threads) * 100

        health_status["checks"]["threads"] = {
            "status": "ok" if thread_usage_percent < 80 else "warning",
            "active_threads": thread_count,
            "max_threads": max_threads,
            "usage_percent": round(thread_usage_percent, 2)
        }
    except Exception as e:
        health_status["checks"]["threads"] = {
            "status": "unknown",
            "message": f"Could not check threads: {str(e)}"
        }

    # 4. Application uptime
    try:
        import time
        if not hasattr(app, '_start_time'):
            app._start_time = time.time()

        uptime_seconds = time.time() - app._start_time
        uptime_hours = uptime_seconds / 3600

        health_status["checks"]["uptime"] = {
            "status": "ok",
            "uptime_hours": round(uptime_hours, 2),
            "uptime_days": round(uptime_hours / 24, 2)
        }
    except Exception as e:
        health_status["checks"]["uptime"] = {
            "status": "unknown",
            "message": f"Could not check uptime: {str(e)}"
        }

    # Determine overall HTTP status code
    if health_status["status"] == "healthy":
        return jsonify(health_status), 200
    elif health_status["status"] == "degraded":
        return jsonify(health_status), 200  # Still operational
    else:
        return jsonify(health_status), 503  # Service unavailable


@app.route("/health/ready")
@limiter.exempt
def readiness_check():
    """Readiness check - is the app ready to receive traffic?

    Returns 200 if database is accessible, 503 otherwise.
    Used by Kubernetes/load balancers to determine if pod is ready.
    """
    try:
        db.session.execute(text("SELECT 1"))
        db.session.commit()
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        return jsonify({"status": "not ready", "error": str(e)}), 503


@app.route("/health/live")
@limiter.exempt
def liveness_check():
    """Liveness check - is the app alive (not deadlocked)?

    Simple endpoint that returns 200 if the app can respond.
    Used by Kubernetes to determine if pod should be restarted.
    """
    return jsonify({"status": "alive"}), 200


@app.route("/health/db-pool")
@limiter.exempt
def db_pool_status():
    """Database connection pool status endpoint.

    Returns detailed information about the database connection pool
    including pool size, checked out connections, and overflow.
    Used for diagnosing connection pool exhaustion issues.
    """
    try:
        pool = db.engine.pool
        pool_status = {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "pool": {
                "size": pool.size(),  # Base pool size
                "checked_out": pool.checkedout(),  # Currently checked out connections
                "overflow": pool.overflow(),  # Current overflow count
                "max_overflow": getattr(pool, '_max_overflow', 'N/A'),  # Max overflow allowed
                "total_capacity": pool.size() + getattr(pool, '_max_overflow', 0),  # Total capacity
                "available": pool.size() + getattr(pool, '_max_overflow', 0) - pool.checkedout(),  # Available connections
                "timeout": getattr(pool, '_timeout', 'N/A'),  # Pool timeout in seconds
            }
        }

        # Calculate utilization percentage
        total_capacity = pool.size() + getattr(pool, '_max_overflow', 0)
        checked_out = pool.checkedout()
        if total_capacity > 0:
            utilization = (checked_out / total_capacity) * 100
            pool_status["pool"]["utilization_percent"] = round(utilization, 2)

            # Warn if pool is over 80% utilized
            if utilization > 80:
                pool_status["status"] = "warning"
                pool_status["message"] = f"Connection pool is {utilization:.1f}% utilized"
            elif utilization > 95:
                pool_status["status"] = "critical"
                pool_status["message"] = f"Connection pool is nearly exhausted at {utilization:.1f}% utilization"

        # Add realtime broadcaster stats if available
        try:
            from app.services.realtime import get_broadcaster
            broadcaster = get_broadcaster()
            pool_status["realtime"] = {
                "connected_users": len(broadcaster.get_connected_users()),
                "total_client_connections": broadcaster.get_client_count(),
            }
        except Exception:
            pool_status["realtime"] = {"status": "unavailable"}

        return jsonify(pool_status), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e)
        }), 500
