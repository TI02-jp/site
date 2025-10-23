"""Simple CLI to poll Waitress thread usage via diagnostics endpoint."""

import argparse
import os
import sys
import time
from typing import Any, Dict

import requests


def _fetch_payload(url: str, token: str | None) -> Dict[str, Any]:
    headers = {}
    if token:
        headers["X-Diagnostics-Token"] = token
    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()
    return response.json()


def monitor(url: str, token: str | None, interval: float) -> None:
    print(f"Polling {url} every {interval}s. Press Ctrl+C to stop.")
    while True:
        try:
            payload = _fetch_payload(url, token)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[error] Failed to fetch diagnostics: {exc}", file=sys.stderr)
            time.sleep(interval)
            continue

        thread_count = payload.get("thread_count")
        waitress_count = payload.get("waitress_thread_count")
        configured = payload.get("configured_threads")
        print(
            f"[threads] total={thread_count} waitress={waitress_count} configured={configured}"
        )
        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Waitress thread usage.")
    parser.add_argument(
        "--url",
        default=os.getenv("DIAGNOSTICS_URL", "http://127.0.0.1:5000/_diagnostics/thread-state"),
        help="Diagnostics endpoint URL.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("DIAGNOSTICS_TOKEN"),
        help="Optional diagnostics shared secret.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("DIAGNOSTICS_INTERVAL", "5")),
        help="Polling interval in seconds.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    monitor(args.url, args.token, args.interval)


if __name__ == "__main__":
    main()
