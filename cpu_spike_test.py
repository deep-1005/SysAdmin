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
import os


def burn_cpu(stop_event: mp.Event) -> None:
    # Keep ALU busy in larger batches, then check stop flag.
    # This avoids expensive cross-process event checks on every iteration.
    x = 0.123456789
    while True:
        for _ in range(2_000_000):
            x = (x * 1.0000001 + 3.1415926) % 97.0
        if stop_event.is_set():
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sustained CPU spike for testing alerts.")
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (mp.cpu_count() or 1) * 2),
        help="Number of worker processes. Default is 2x logical cores for stronger spikes.",
    )
    parser.add_argument("--seconds", type=int, default=0, help="Run duration in seconds. 0 means until Ctrl+C.")
    parser.add_argument("--log-interval", type=int, default=5, help="Status print interval in seconds.")
    args = parser.parse_args()

    workers = max(1, args.workers)
    log_interval = max(1, int(args.log_interval or 5))
    stop_event = mp.Event()
    procs: list[mp.Process] = []
    stop_reason = "manual stop"

    def shutdown(*_):
        nonlocal stop_reason
        stop_reason = "signal received (Ctrl+C / terminate)"
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[cpu_spike_test] Controller PID: {os.getpid()}")
    print(f"[cpu_spike_test] Starting {workers} workers. Press Ctrl+C to stop.")
    if args.seconds > 0:
        print(f"[cpu_spike_test] Auto-stop after {args.seconds} seconds.")
    else:
        print("[cpu_spike_test] No auto-timeout configured. Running until manually stopped.")

    for _ in range(workers):
        p = mp.Process(target=burn_cpu, args=(stop_event,), daemon=False)
        p.start()
        procs.append(p)

    try:
        if args.seconds > 0:
            end_at = time.time() + args.seconds
            next_log = time.time() + log_interval
            while time.time() < end_at and not stop_event.is_set():
                if time.time() >= next_log:
                    remaining = max(0, int(end_at - time.time()))
                    print(f"[cpu_spike_test] alive, remaining={remaining}s")
                    next_log = time.time() + log_interval
                time.sleep(0.2)
            if not stop_event.is_set():
                stop_reason = f"timeout reached ({args.seconds}s)"
            stop_event.set()
        else:
            next_log = time.time() + log_interval
            while not stop_event.is_set():
                if time.time() >= next_log:
                    print("[cpu_spike_test] alive, waiting for manual stop")
                    next_log = time.time() + log_interval
                time.sleep(0.2)
    finally:
        stop_event.set()
        for p in procs:
            p.join(timeout=2)
        print(f"[cpu_spike_test] Stopped. reason={stop_reason}")


if __name__ == "__main__":
    main()
