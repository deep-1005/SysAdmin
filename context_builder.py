class ContextBuilder:
    def __init__(self, log_file="logs/system.log"):
        self.log_file = log_file
        # Seed/demo lines that can exist in a fresh project log file.
        # They should not continuously trigger production alerts.
        self._seed_lines = {
            "system boot complete",
            "connection refused from localhost:5000",
            "out of memory: killed process 4213 (python)",
            "disk warning: write latency increasing",
            "segmentation fault in service.exe",
        }

    def read_recent_logs(self, num_lines=5):
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return [line.strip() for line in lines[-num_lines:]]
        except FileNotFoundError:
            return []

    def has_log_alert(self, logs):
        alert_keywords = [
            "out of memory",
            "segmentation fault",
            "connection refused",
            "disk warning",
            "i/o error",
            "killed process"
        ]

        for log in logs:
            lower_log = log.lower()
            if lower_log in self._seed_lines:
                continue
            for keyword in alert_keywords:
                if keyword in lower_log:
                    return True
        return False

    def build_context(self, metrics, primary_event, detected_events):
        recent_logs = self.read_recent_logs()

        if "NORMAL" in detected_events and self.has_log_alert(recent_logs):
            primary_event = "LOG_ALERT"
            detected_events = ["LOG_ALERT"]

        return {
            "cpu_usage": metrics["cpu_usage"],
            "memory_usage": metrics["memory_usage"],
            "disk_usage": metrics["disk_usage"],
            "process_count": metrics["process_count"],
            "primary_event": primary_event,
            "detected_events": detected_events,
            "recent_logs": recent_logs,
            "steps_taken": []
        }