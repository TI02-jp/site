"""Parse application logs to surface slow request patterns."""

import argparse
import ast
import collections
from pathlib import Path
from typing import Dict, List


def _parse_payload(line: str) -> Dict:
    marker = "SLOW REQUEST:"
    if marker not in line:
        return {}
    try:
        fragment = line.split(marker, 1)[1].strip()
        return ast.literal_eval(fragment)
    except (IndexError, SyntaxError, ValueError):
        return {}


def analyze(log_path: Path) -> None:
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    by_path: Dict[str, List[float]] = collections.defaultdict(list)
    sql_time_by_path: Dict[str, List[float]] = collections.defaultdict(list)
    slow_queries: List[Dict] = []

    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            payload = _parse_payload(line)
            if not payload:
                continue
            path = payload.get("path", "unknown")
            duration = float(payload.get("duration_ms", 0.0))
            sql_time = float(payload.get("sql_time_ms", 0.0))
            by_path[path].append(duration)
            sql_time_by_path[path].append(sql_time)
            slow_queries.extend(payload.get("sql_queries", []))

    if not by_path:
        print("No SLOW REQUEST entries found.")
        return

    print("Top endpoints by average duration:")
    ranked = sorted(
        by_path.items(),
        key=lambda item: (sum(item[1]) / len(item[1])),
        reverse=True,
    )[:10]
    for path, values in ranked:
        average = sum(values) / len(values)
        sql_average = sum(sql_time_by_path[path]) / len(sql_time_by_path[path])
        print(
            f" - {path}: avg={average:.1f}ms (count={len(values)} slow events, avg sql={sql_average:.1f}ms)"
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    analyze(Path(args.log))


if __name__ == "__main__":
    main()
