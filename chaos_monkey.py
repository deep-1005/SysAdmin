"""
chaos_monkey.py — Windows Chaos Simulator
──────────────────────────────────────────
Triggers real system load to test the SysAdmin agent.
Run in a SEPARATE terminal, then watch the dashboard react.

Usage:
    python chaos_monkey.py cpu       # burn CPU on all cores
    python chaos_monkey.py memory    # slowly leak RAM
    python chaos_monkey.py disk      # fill temp drive
    python chaos_monkey.py zombie    # flood process table

CTRL+C to stop and clean up.
Works on Windows with no admin rights required.
"""
import sys, os, time, multiprocessing, tempfile

# ── modes ─────────────────────────────────────────────────────

def cpu_spike():
    """Burn all CPU cores using pure Python math."""
    cores = multiprocessing.cpu_count()
    print(f"[chaos] Spiking CPU on {cores} cores. CTRL+C to stop.")

    def burn(stop):
        while not stop.is_set():
            _ = sum(i * i for i in range(50_000))

    stop = multiprocessing.Event()
    workers = [multiprocessing.Process(target=burn, args=(stop,)) for _ in range(cores)]
    for w in workers: w.start()
    try:
        while True: time.sleep(0.5)
    except KeyboardInterrupt:
        stop.set()
        for w in workers: w.terminate()
        print("[chaos] CPU spike stopped.")


def memory_leak():
    """Slowly allocate RAM until killed or CTRL+C."""
    print("[chaos] Starting memory leak (10 MB/s). CTRL+C to stop.")
    chunks = []
    try:
        while True:
            chunks.append(bytearray(10 * 1024 * 1024))   # 10 MB
            print(f"[chaos] RAM consumed: {len(chunks) * 10} MB")
            time.sleep(1)
    except KeyboardInterrupt:
        print("[chaos] Freeing memory…")
        del chunks
        print("[chaos] Memory leak stopped.")


def disk_fill():
    """Write junk files to the Windows TEMP folder."""
    tmp = tempfile.gettempdir()
    print(f"[chaos] Writing junk to {tmp}. CTRL+C to stop.")
    files = []
    try:
        i = 0
        while True:
            path = os.path.join(tmp, f"chaos_{i}.junk")
            with open(path, "wb") as f:
                f.write(os.urandom(50 * 1024 * 1024))    # 50 MB
            files.append(path)
            print(f"[chaos] Written {path}  ({(i+1)*50} MB total)")
            i += 1
            time.sleep(2)
    except KeyboardInterrupt:
        print("[chaos] Cleaning up temp files…")
        for fp in files:
            try: os.remove(fp)
            except: pass
        print("[chaos] Disk fill stopped.")


def zombie_flood():
    """Spawn hundreds of sleeping processes to flood the process table."""
    print("[chaos] Spawning 250 idle processes. CTRL+C to stop.")
    procs = []
    try:
        for i in range(250):
            p = multiprocessing.Process(target=time.sleep, args=(99999,))
            p.start()
            procs.append(p)
            if i % 25 == 0:
                print(f"[chaos] {i+1} processes spawned…")
            time.sleep(0.04)
        print(f"[chaos] {len(procs)} idle processes running. CTRL+C to kill.")
        while True: time.sleep(1)
    except KeyboardInterrupt:
        for p in procs: p.terminate()
        print("[chaos] All zombie processes terminated.")


# ── dispatch ──────────────────────────────────────────────────
MODES = {"cpu": cpu_spike, "memory": memory_leak, "disk": disk_fill, "zombie": zombie_flood}

if __name__ == "__main__":
    # Windows multiprocessing requires this guard
    multiprocessing.freeze_support()
    mode = sys.argv[1] if len(sys.argv) > 1 else "cpu"
    if mode not in MODES:
        print(f"Usage: python chaos_monkey.py [{' | '.join(MODES)}]")
        sys.exit(1)
    MODES[mode]()
