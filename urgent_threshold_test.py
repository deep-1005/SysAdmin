"""
urgent_threshold_test.py

Purpose:
Generate an URGENT incident for SysAdmin monitor by forcing:
1) high CPU load, and
2) high memory pressure.

This is for local testing only.

Examples:
  python urgent_threshold_test.py
  python urgent_threshold_test.py --seconds 180 --target-mem-percent 92
  python urgent_threshold_test.py --cpu-workers 16 --chunk-mb 128
"""

import argparse
import multiprocessing as mp
import signal
import time

import psutil


def burn_cpu(stop_event: mp.Event) -> None:
    x = 0.123456789
    while not stop_event.is_set():
        # Tight loop to keep core utilization high.
        x = (x * 1.0000003 + 3.1415926) % 1000.0


def hog_memory(stop_event: mp.Event, target_mem_percent: float, chunk_mb: int) -> None:
    chunks = []
    chunk_bytes = max(1, chunk_mb) * 1024 * 1024

    while not stop_event.is_set():
        vm = psutil.virtual_memory()
        if vm.percent >= target_mem_percent:
            # Hold memory once target pressure is reached.
            time.sleep(0.2)
            continue

        try:
            chunks.append(bytearray(chunk_bytes))
            # Touch memory pages so allocation is realized.
            chunks[-1][0] = 1
            chunks[-1][-1] = 1
        except MemoryError:
            # If allocator refuses more memory, keep holding what we got.
            time.sleep(0.5)

    # Release on shutdown.
    chunks.clear()


def main() -> None:
    parser = argparse.ArgumentParser(description="Force urgent CPU+memory thresholds for alert testing.")
    parser.add_argument("--cpu-workers", type=int, default=max(1, psutil.cpu_count(logical=True) or 1),
                        help="Number of CPU worker processes.")
    parser.add_argument("--seconds", type=int, default=180,
                        help="How long to run (seconds).")
    parser.add_argument("--target-mem-percent", type=float, default=92.0,
                        help="Memory usage percent target to hold.")
    parser.add_argument("--chunk-mb", type=int, default=64,
                        help="Memory allocation chunk size in MB.")
    args = parser.parse_args()

    stop_event = mp.Event()
    procs: list[mp.Process] = []

    def shutdown(*_):
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[urgent_threshold_test] Starting urgent load generator...")
    print(f"[urgent_threshold_test] CPU workers: {args.cpu_workers}")
    print(f"[urgent_threshold_test] Memory target: {args.target_mem_percent:.1f}%")
    print(f"[urgent_threshold_test] Duration: {args.seconds}s")

    # Start CPU workers.
    for _ in range(max(1, args.cpu_workers)):
        p = mp.Process(target=burn_cpu, args=(stop_event,), daemon=True)
        p.start()
        procs.append(p)

    # Start memory hog worker.
    mem_p = mp.Process(
        target=hog_memory,
        args=(stop_event, float(args.target_mem_percent), int(args.chunk_mb)),
        daemon=True,
    )
    mem_p.start()
    procs.append(mem_p)

    end_at = time.time() + max(1, args.seconds)
    try:
        while time.time() < end_at and not stop_event.is_set():
            vm = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.5)
            print(f"[urgent_threshold_test] CPU={cpu:.1f}% MEM={vm.percent:.1f}%")
    finally:
        stop_event.set()
        for p in procs:
            p.join(timeout=3)
        print("[urgent_threshold_test] Stopped.")


if __name__ == "__main__":
    main()
