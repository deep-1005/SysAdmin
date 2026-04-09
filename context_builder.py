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
        # Guard LOG_ALERT escalation to avoid noisy overnight alerts when metrics are calm.
        self._log_guard_cpu = 60.0
        self._log_guard_memory = 70.0
        self._log_guard_process_ratio = 0.80

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

    def _metrics_support_log_alert(self, metrics):
        cpu = float(metrics.get("cpu_usage", 0.0) or 0.0)
        memory = float(metrics.get("memory_usage", 0.0) or 0.0)
        process_count = float(metrics.get("process_count", 0.0) or 0.0)
        process_threshold = float(metrics.get("process_count_threshold", 300.0) or 300.0)
        process_ratio = (process_count / process_threshold) if process_threshold > 0 else 0.0
        return (
            cpu >= self._log_guard_cpu
            or memory >= self._log_guard_memory
            or process_ratio >= self._log_guard_process_ratio
        )

    def build_context(self, metrics, primary_event, detected_events):
        recent_logs = self.read_recent_logs()

        if (
            "NORMAL" in detected_events
            and self.has_log_alert(recent_logs)
            and self._metrics_support_log_alert(metrics)
        ):
            primary_event = "LOG_ALERT"
            detected_events = ["LOG_ALERT"]

        return {
            "cpu_usage": metrics["cpu_usage"],
            "memory_usage": metrics["memory_usage"],
            "disk_usage": metrics["disk_usage"],
            "process_count": metrics["process_count"],
            "process_count_threshold": metrics.get("process_count_threshold", 300),
            "primary_event": primary_event,
            "detected_events": detected_events,
            "recent_logs": recent_logs,
            "steps_taken": []
        }