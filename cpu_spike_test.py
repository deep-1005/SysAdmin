"""
cpu_spike_test.py

Intentional CPU stress tool for validating SysAdmin alerting.
Use only on your own machine.

Usage:
  python cpu_spike_test.py
  python cpu_spike_test.py --seconds 120
  python cpu_spike_test.py --workers 8 --seconds 90
"""

import argparse
import multiprocessing as mp
import signal
import time


def burn_cpu(stop_event: mp.Event) -> None:
    # Tight math loop to keep a core busy.
    x = 0.123456789
    while not stop_event.is_set():
        x = (x * 1.0000001 + 3.1415926) % 97.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sustained CPU spike for testing alerts.")
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count()), help="Number of worker processes.")
    parser.add_argument("--seconds", type=int, default=0, help="Run duration in seconds. 0 means until Ctrl+C.")
    args = parser.parse_args()

    workers = max(1, args.workers)
    stop_event = mp.Event()
    procs: list[mp.Process] = []

    def shutdown(*_):
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[cpu_spike_test] Starting {workers} workers. Press Ctrl+C to stop.")
    if args.seconds > 0:
        print(f"[cpu_spike_test] Auto-stop after {args.seconds} seconds.")

    for _ in range(workers):
        p = mp.Process(target=burn_cpu, args=(stop_event,), daemon=True)
        p.start()
        procs.append(p)

    try:
        if args.seconds > 0:
            end_at = time.time() + args.seconds
            while time.time() < end_at and not stop_event.is_set():
                time.sleep(0.2)
            stop_event.set()
        else:
            while not stop_event.is_set():
                time.sleep(0.2)
    finally:
        stop_event.set()
        for p in procs:
            p.join(timeout=2)
        print("[cpu_spike_test] Stopped.")


if __name__ == "__main__":
    main()
