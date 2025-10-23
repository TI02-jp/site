"""Parse application logs to surface slow request patterns.

Enhancements compared to the initial version:
- Aggregates custom span timings (e.g., sidebar, render_home)
- Reports top endpoints and SQL statements
- Allows filtering by request path or minimum duration
"""

from __future__ import annotations

import argparse
import ast
import collections
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def _parse_payload(line: str) -> Dict:
    marker = "SLOW REQUEST:"
    if marker not in line:
        return {}
    try:
        fragment = line.split(marker, 1)[1].strip()
        return ast.literal_eval(fragment)
    except (IndexError, SyntaxError, ValueError):
        return {}


def _average(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        return 0.0
    return sum(values) / len(values)


def analyze(log_path: Path, *, min_duration: float = 0.0, filter_path: str | None = None) -> None:
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    by_path: Dict[str, List[float]] = collections.defaultdict(list)
    sql_time_by_path: Dict[str, List[float]] = collections.defaultdict(list)
    span_stats: Dict[Tuple[str, str], List[float]] = collections.defaultdict(list)
    slow_queries: List[Dict] = []

    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            payload = _parse_payload(line)
            if not payload:
                continue
            duration = float(payload.get("duration_ms", 0.0))
            if duration < min_duration:
                continue
            path = payload.get("path", "unknown")
            if filter_path and filter_path not in path:
                continue
            sql_time = float(payload.get("sql_time_ms", 0.0))

            by_path[path].append(duration)
            sql_time_by_path[path].append(sql_time)
            slow_queries.extend(payload.get("sql_queries", []))

            for span in payload.get("custom_spans", []):
                category = span.get("category", "unknown")
                name = span.get("name", "unknown")
                span_duration = float(span.get("duration_ms", 0.0))
                span_stats[(category, name)].append(span_duration)

    if not by_path:
        print("No SLOW REQUEST entries found matching filters.")
        return

    print("Top endpoints by average duration:")
    ranked = sorted(
        by_path.items(),
        key=lambda item: _average(item[1]),
        reverse=True,
    )[:10]
    for path, values in ranked:
        average = _average(values)
        sql_average = _average(sql_time_by_path[path])
        print(
            f" - {path}: avg={average:.1f}ms (count={len(values)} slow events, avg sql={sql_average:.1f}ms)"
        )

    if span_stats:
        print("\nTop custom spans by average duration:")
        ranked_spans = sorted(
            span_stats.items(),
            key=lambda item: _average(item[1]),
            reverse=True,
        )[:10]
        for (category, name), durations in ranked_spans:
            avg = _average(durations)
            max_span = max(durations)
            count = len(durations)
            print(
                f" - {category}:{name} avg={avg:.1f}ms max={max_span:.1f}ms (count={count})"
            )

    if slow_queries:
        print("\nMost frequent slow SQL statements:")
        counter = collections.Counter(
            entry.get("sql", "unknown") for entry in slow_queries
        )
        for statement, count in counter.most_common(5):
            print(f" - {count} hits: {statement}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze slow request entries from logs.")
    parser.add_argument(
        "--log",
        default="logs/app.log",
        help="Path to the log file (default: logs/app.log).",
    )
    parser.add_argument(
        "--min-duration",
        type=float,
        default=0.0,
        help="Only include entries with total duration >= value (ms).",
    )
    parser.add_argument(
        "--filter-path",
        default=None,
        help="Only include entries whose path contains this substring.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="If > 0, rerun analysis every N seconds (Ctrl+C to stop).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_path = Path(args.log)
    if args.interval > 0:
        import time

        print(f"Watching {log_path} (interval={args.interval}s). Press Ctrl+C to stop.\n")
        try:
            while True:
                print("=" * 80)
                print(time.strftime("%Y-%m-%d %H:%M:%S"))
                analyze(
                    log_path,
                    min_duration=args.min_duration,
                    filter_path=args.filter_path,
                )
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped watcher.")
    else:
        analyze(
            log_path,
            min_duration=args.min_duration,
            filter_path=args.filter_path,
        )


if __name__ == "__main__":
    main()
