import os
import sys
import psutil


class Watcher:
    def __init__(
        self,
        cpu_threshold=80,
        memory_threshold=85,
        disk_threshold=95,          # raised — Windows C: drives often sit at 90%+
        process_count_threshold=300,
        disk_path=None,
    ):
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.disk_threshold = disk_threshold
        self.process_count_threshold = process_count_threshold
        self.disk_path = disk_path or os.getenv("MONITOR_DRIVE")
        if not self.disk_path:
            self.disk_path = "C:\\" if sys.platform == "win32" else "/"

    def get_metrics(self):
        cpu_usage = psutil.cpu_percent(interval=1)
        memory_usage = psutil.virtual_memory().percent

        try:
            disk_usage = psutil.disk_usage(self.disk_path).percent
        except Exception:
            disk_usage = psutil.disk_usage("/").percent

        process_count = len(psutil.pids())

        return {
            "cpu_usage": round(cpu_usage, 2),
            "memory_usage": round(memory_usage, 2),
            "disk_usage": round(disk_usage, 2),
            "process_count": process_count,
            "process_count_threshold": self.process_count_threshold,
        }

    def detect_events(self, metrics):
        detected_events = []

        if metrics["cpu_usage"] >= self.cpu_threshold:
            detected_events.append("CPU_SPIKE")
        if metrics["memory_usage"] >= self.memory_threshold:
            detected_events.append("MEMORY_SPIKE")
        if metrics["disk_usage"] >= self.disk_threshold:
            detected_events.append("DISK_SPIKE")
        if metrics["process_count"] >= self.process_count_threshold:
            detected_events.append("HIGH_PROCESS_COUNT")

        if not detected_events:
            detected_events.append("NORMAL")

        primary_event = detected_events[0]

        return primary_event, detected_events