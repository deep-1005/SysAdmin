"""Quick regression fixture checker for incident detection from log snapshots."""

import glob
import os

from context_builder import ContextBuilder


def main() -> int:
    root = os.path.dirname(__file__)
    fixture_dir = os.path.join(root, "fixtures", "log_snapshots")
    files = sorted(glob.glob(os.path.join(fixture_dir, "*.log")))
    if not files:
        print("No fixture snapshots found")
        return 0

    builder = ContextBuilder()
    base_metrics = {
        "cpu_usage": 86.0,
        "memory_usage": 90.0,
        "disk_usage": 94.0,
        "process_count": 360,
        "process_count_threshold": 300,
        "risk_level": "dangerous",
        "anomaly": {},
    }

    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        has_alert = builder.has_log_alert(lines)
        print(f"{os.path.basename(path)} -> log_alert={has_alert}")

    ctx = builder.build_context(base_metrics, "NORMAL", ["NORMAL"])
    print("Synthetic context primary_event:", ctx["primary_event"])
    print("Synthetic risk_level:", ctx["risk_level"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
