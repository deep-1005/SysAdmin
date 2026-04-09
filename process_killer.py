import subprocess
import os
import psutil


def _script_basename_for_pid(pid: int) -> str:
    """Return script basename for a python process cmdline, if any."""
    try:
        proc = psutil.Process(pid)
        cmd = proc.cmdline() or []
    except Exception:
        return ""

    for part in cmd:
        try:
            token = str(part).strip().strip('"').lower()
        except Exception:
            continue
        if token.endswith(".py"):
            return os.path.basename(token)
    return ""


def _related_python_pids_by_script(script_basename: str, blocked_pids: set[int]) -> list[int]:
    """Find running python PIDs with the same script basename in cmdline."""
    if not script_basename:
        return []

    out: list[int] = []
    target = script_basename.lower()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pid = int(p.info.get("pid") or 0)
            if pid in blocked_pids:
                continue

            name = (p.info.get("name") or "").lower()
            if "python" not in name:
                continue

            cmd = p.cmdline() or []
            has_script = any(str(c).strip().strip('"').lower().endswith(target) for c in cmd)
            if has_script:
                out.append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue

    return out


def describe_process(pid: int) -> dict:
    details = {"name": "unknown", "exe": "(unavailable)", "cmd": "(unavailable)"}
    try:
        proc = psutil.Process(pid)
        details["name"] = proc.name() or "unknown"
        try:
            details["exe"] = proc.exe() or "(unavailable)"
        except Exception:
            pass
        try:
            cmdline = proc.cmdline() or []
            details["cmd"] = " ".join(cmdline) if cmdline else "(unavailable)"
        except Exception:
            pass
    except Exception:
        pass
    return details


def resolve_termination_target(pid: int, blocked_pids: set[int] | None = None) -> int:
    """Pick the best PID to terminate, preferring Python job roots over child workers."""
    blocked = set(blocked_pids or set())
    blocked.update({0, 4, os.getpid()})

    if pid in blocked:
        return pid

    try:
        proc = psutil.Process(pid)
    except Exception:
        return pid

    chosen = pid
    ancestry: list[psutil.Process] = []
    cur = proc
    # Walk parents up to a safe depth.
    for _ in range(8):
        ancestry.append(cur)
        try:
            parent = cur.parent()
        except Exception:
            break
        if parent is None:
            break
        if parent.pid in blocked:
            break
        cur = parent

    def _name(p: psutil.Process) -> str:
        try:
            return (p.name() or "").lower()
        except Exception:
            return ""

    def _cmd(p: psutil.Process) -> str:
        try:
            cmdline = p.cmdline() or []
            return " ".join(cmdline).lower()
        except Exception:
            return ""

    # Prefer explicit test launcher roots when present.
    for p in reversed(ancestry):
        cmd = _cmd(p)
        if "urgent_threshold_test.py" in cmd:
            return p.pid

    # If target is a Python worker process, move up to the top Python parent
    # (but stop before shell/terminal parents).
    try:
        target_is_python = "python" in _name(proc)
    except Exception:
        target_is_python = False

    if target_is_python:
        for p in reversed(ancestry):
            if p.pid in blocked:
                continue
            if "python" in _name(p):
                chosen = p.pid
                break

    return chosen


def terminate_process_tree(pid: int, timeout_s: int = 3) -> dict:
    """Terminate a process and its children; fallback to taskkill on Windows."""
    errors: list[str] = []
    blocked = {0, 4, os.getpid()}

    pid = resolve_termination_target(pid, blocked_pids=blocked)
    script_basename = _script_basename_for_pid(pid)

    def _gone() -> bool:
        return not psutil.pid_exists(pid)

    if _gone():
        return {"terminated": True, "method": "already-gone", "error": "", "errors": []}

    # Preemptively terminate sibling python jobs for the same script if present.
    related_pids = [rp for rp in _related_python_pids_by_script(script_basename, blocked) if rp != pid]
    for rp in related_pids:
        try:
            subprocess.run(["taskkill", "/PID", str(rp), "/T", "/F"], check=False, capture_output=True, text=True)
        except Exception:
            pass

    try:
        proc = psutil.Process(pid)
        children = []
        try:
            children = proc.children(recursive=True)
        except Exception:
            children = []

        for child in children:
            try:
                child.kill()
            except Exception:
                pass

        try:
            proc.terminate()
            proc.wait(timeout=timeout_s)
        except psutil.TimeoutExpired:
            for child in children:
                try:
                    child.kill()
                except Exception:
                    pass
            proc.kill()
            proc.wait(timeout=timeout_s)

        if _gone():
            return {"terminated": True, "method": "psutil", "error": "", "errors": []}
    except Exception as exc:
        errors.append(f"psutil terminate failed: {exc}")

    try:
        cp = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        if cp.returncode == 0 and _gone():
            return {"terminated": True, "method": "taskkill", "error": "", "errors": errors}
        if cp.returncode != 0:
            err = (cp.stderr or cp.stdout or "unknown error").strip()
            errors.append(f"taskkill failed: {err}")
    except Exception as exc:
        errors.append(f"taskkill launch failed: {exc}")

    # Final sweep for same-script sibling processes that might still be alive.
    for rp in _related_python_pids_by_script(script_basename, blocked):
        if rp == pid:
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(rp), "/T", "/F"], check=False, capture_output=True, text=True)
        except Exception:
            pass

    final_error = "; ".join(errors) if errors else "process is still running"
    return {"terminated": _gone(), "method": "failed", "error": final_error, "errors": errors}
