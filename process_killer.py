import subprocess
import psutil


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


def terminate_process_tree(pid: int, timeout_s: int = 3) -> dict:
    """Terminate a process and its children; fallback to taskkill on Windows."""
    errors: list[str] = []

    def _gone() -> bool:
        return not psutil.pid_exists(pid)

    if _gone():
        return {"terminated": True, "method": "already-gone", "error": "", "errors": []}

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

    final_error = "; ".join(errors) if errors else "process is still running"
    return {"terminated": _gone(), "method": "failed", "error": final_error, "errors": errors}
