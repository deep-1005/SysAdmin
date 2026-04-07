"""
tool_runner.py — Windows-safe diagnostic sandbox
─────────────────────────────────────────────────
All tools are READ-ONLY. No shell=True. No admin needed.
Uses psutil instead of Unix commands so it works on Windows.
"""
import psutil
import time


class ToolRunner:
    def __init__(self):
        self.allowed_tools = {
            "check_processes":      self.check_processes,
            "check_memory":         self.check_memory,
            "check_disk":           self.check_disk,
            "inspect_top_process":  self.inspect_top_process,
            "check_open_files":     self.check_open_files,
            "check_network":        self.check_network,
        }

    def run_tool(self, tool_name: str) -> str:
        if tool_name not in self.allowed_tools:
            return f"[ERROR] '{tool_name}' is not in the whitelist."
        try:
            return self.allowed_tools[tool_name]()
        except Exception as e:
            return f"[ERROR] {tool_name} failed: {e}"

    # ── tools ──────────────────────────────────────────────

    # Windows pseudo-processes that always show misleading CPU — skip them
    SKIP_NAMES = {"system idle process", "system", "registry", "memory compression"}
    SKIP_PIDS  = {0, 4}   # PID 0 = Idle, PID 4 = System kernel

    def _safe_process_info(self, attrs):
        procs = []
        for p in psutil.process_iter():
            try:
                info = p.as_dict(attrs=attrs)
                if info.get("pid") in self.SKIP_PIDS:
                    continue
                if (info.get("name") or "").lower() in self.SKIP_NAMES:
                    continue
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        return procs

    def _safe_process_info_with_cpu(self):
        # psutil returns 0.0 on first call; prime once, wait briefly, then sample.
        for p in psutil.process_iter():
            try:
                p.cpu_percent(interval=None)
            except Exception:
                continue

        time.sleep(0.25)

        procs = []
        for p in psutil.process_iter():
            try:
                info = p.as_dict(attrs=["pid", "name", "memory_percent", "status"])
                if info.get("pid") in self.SKIP_PIDS:
                    continue
                if (info.get("name") or "").lower() in self.SKIP_NAMES:
                    continue
                info["cpu_percent"] = p.cpu_percent(interval=None)
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        return procs

    def check_processes(self) -> str:
        procs = self._safe_process_info_with_cpu()
        procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
        lines = ["Top 5 processes by CPU:"]
        for p in procs[:5]:
            lines.append(
                f"  PID={p['pid']:<6} "
                f"NAME={p['name']:<22} "
                f"CPU={p['cpu_percent']:>5.1f}%  "
                f"MEM={round(p['memory_percent'], 2):>5.2f}%  "
                f"STATUS={p['status']}"
            )
        return "\n".join(lines)

    def check_memory(self) -> str:
        m = psutil.virtual_memory()
        s = psutil.swap_memory()
        return (
            f"Memory Summary:\n"
            f"  Total    : {m.total / 1e9:.2f} GB\n"
            f"  Used     : {m.used / 1e9:.2f} GB  ({m.percent}%)\n"
            f"  Available: {m.available / 1e9:.2f} GB\n"
            f"  Swap used: {s.used / 1e9:.2f} GB / {s.total / 1e9:.2f} GB"
        )

    def check_disk(self) -> str:
        lines = ["Disk Usage (all drives):"]
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                lines.append(
                    f"  {part.device:<8}  "
                    f"Total={u.total / 1e9:.1f}GB  "
                    f"Used={u.used / 1e9:.1f}GB  "
                    f"Free={u.free / 1e9:.1f}GB  "
                    f"({u.percent}%)"
                )
            except PermissionError:
                lines.append(f"  {part.device} — access denied")
        return "\n".join(lines)

    def inspect_top_process(self) -> str:
        procs = self._safe_process_info_with_cpu()
        procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
        if not procs:
            return "No processes found."
        t = procs[0]

        try:
            proc = psutil.Process(t["pid"])
            details = proc.as_dict(attrs=["num_threads", "cmdline", "exe"])
        except Exception:
            details = {"num_threads": "N/A", "cmdline": [], "exe": "(unavailable)"}

        cmd = " ".join(details.get("cmdline") or []) or "(unavailable)"
        exe = details.get("exe") or "(unavailable)"
        return (
            f"Top Process Inspection:\n"
            f"  PID     : {t['pid']}\n"
            f"  Name    : {t['name']}\n"
            f"  EXE     : {exe[:60]}\n"
            f"  CMD     : {cmd[:60]}\n"
            f"  CPU     : {t['cpu_percent']}%\n"
            f"  Memory  : {round(t['memory_percent'], 2)}%\n"
            f"  Status  : {t['status']}\n"
            f"  Threads : {details.get('num_threads', 'N/A')}"
        )

    def check_open_files(self) -> str:
        procs = self._safe_process_info_with_cpu()
        procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
        if not procs:
            return "No processes."
        top = procs[0]
        try:
            proc  = psutil.Process(top["pid"])
            files = proc.open_files()
            conns = proc.connections()
            sample = ", ".join(f.path for f in files[:2]) or "none"
            return (
                f"Open handles for PID {top['pid']} ({top['name']}):\n"
                f"  Open files  : {len(files)}\n"
                f"  Connections : {len(conns)}\n"
                f"  Sample      : {sample}"
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            return f"Cannot inspect PID {top['pid']}: {e}"

    def check_network(self) -> str:
        conns = psutil.net_connections(kind="inet")
        est = [c for c in conns if c.status == "ESTABLISHED"]
        lst = [c for c in conns if c.status == "LISTEN"]
        lines = [f"Network ({len(conns)} total connections):"]
        lines.append(f"  ESTABLISHED : {len(est)}")
        lines.append(f"  LISTENING   : {len(lst)}")
        for c in lst[:5]:
            port = c.laddr.port if c.laddr else "?"
            lines.append(f"    Listening on :{port}")
        return "\n".join(lines)
