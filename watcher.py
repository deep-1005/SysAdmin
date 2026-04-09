import os
import sys
import math
import time
from collections import deque
from datetime import datetime
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

        self.zscore_threshold = float(os.getenv("ANOMALY_ZSCORE_THRESHOLD", "2.2"))
        self.trend_delta_threshold = float(os.getenv("ANOMALY_TREND_DELTA", "6.0"))
        self._rolling_window = int(os.getenv("ANOMALY_ROLLING_WINDOW", "18"))
        self._slot_window = int(os.getenv("ANOMALY_SLOT_WINDOW", "45"))
        self.cpu_sample_interval = float(os.getenv("CPU_SAMPLE_INTERVAL", "1.0"))
        self._cpu_smoothing_window = int(os.getenv("CPU_SMOOTHING_WINDOW", "1"))

        self._history = {
            "cpu_usage": deque(maxlen=720),
            "memory_usage": deque(maxlen=720),
            "disk_usage": deque(maxlen=720),
            "process_count": deque(maxlen=720),
        }
        self._cpu_samples = deque(maxlen=max(1, self._cpu_smoothing_window))
        self._slot_history = {
            "cpu_usage": {},
            "memory_usage": {},
            "disk_usage": {},
            "process_count": {},
        }

        # Prime CPU sampling once to avoid first-read artifacts and keep sampling non-blocking.
        self._last_cpu_usage = 0.0
        self._last_sample_ts = time.time()
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

    def _time_slot(self) -> str:
        now = datetime.now()
        return f"{now.weekday()}-{now.hour}"

    def _mean_std(self, values) -> tuple[float, float]:
        if not values:
            return 0.0, 1.0
        mean = sum(values) / len(values)
        if len(values) < 2:
            return mean, 1.0
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        std = math.sqrt(max(variance, 1e-6))
        return mean, max(std, 1.0)

    def _update_baselines(self, metrics: dict) -> dict:
        slot = self._time_slot()
        anomaly = {}

        for key in self._history:
            value = float(metrics.get(key, 0.0) or 0.0)
            hist = self._history[key]
            hist.append(value)

            slot_hist_map = self._slot_history[key]
            if slot not in slot_hist_map:
                slot_hist_map[slot] = deque(maxlen=self._slot_window)
            slot_hist = slot_hist_map[slot]
            slot_hist.append(value)

            baseline_source = list(slot_hist) if len(slot_hist) >= 8 else list(hist)
            mean, std = self._mean_std(baseline_source)
            z = (value - mean) / std if std > 0 else 0.0

            trend = 0.0
            rolling = list(hist)[-self._rolling_window :]
            if len(rolling) >= 6:
                half = len(rolling) // 2
                first = sum(rolling[:half]) / max(1, half)
                second = sum(rolling[half:]) / max(1, len(rolling) - half)
                trend = second - first

            anomaly[key] = {
                "value": round(value, 2),
                "baseline_mean": round(mean, 2),
                "baseline_std": round(std, 2),
                "zscore": round(z, 2),
                "trend_delta": round(trend, 2),
                "is_spike": bool(z >= self.zscore_threshold and trend >= self.trend_delta_threshold),
            }

        return anomaly

    def _risk_level(self, metrics: dict, anomalies: dict) -> str:
        severe_spikes = sum(1 for k in anomalies if anomalies[k].get("is_spike"))

        cpu = float(metrics.get("cpu_usage", 0.0) or 0.0)
        mem = float(metrics.get("memory_usage", 0.0) or 0.0)
        disk = float(metrics.get("disk_usage", 0.0) or 0.0)
        proc = int(metrics.get("process_count", 0) or 0)

        if severe_spikes >= 2 or cpu >= 92 or mem >= 94 or disk >= 98 or proc >= int(self.process_count_threshold * 1.25):
            return "dangerous"
        if severe_spikes >= 1 or cpu >= 82 or mem >= 86 or disk >= 95 or proc >= int(self.process_count_threshold * 1.05):
            return "caution"
        return "safe"

    def get_metrics(self):
        sampled_at = time.time()

        # Non-blocking CPU sample so all metrics are captured from the same refresh cycle.
        try:
            cpu_usage = float(psutil.cpu_percent(interval=self.cpu_sample_interval) or 0.0)
        except Exception:
            cpu_usage = self._last_cpu_usage
        if cpu_usage == 0.0 and (sampled_at - self._last_sample_ts) < 0.5:
            cpu_usage = self._last_cpu_usage
        self._last_cpu_usage = cpu_usage
        self._last_sample_ts = sampled_at
        self._cpu_samples.append(cpu_usage)
        cpu_smoothed = sum(self._cpu_samples) / len(self._cpu_samples)

        vm = psutil.virtual_memory()
        memory_usage = vm.percent

        try:
            disk = psutil.disk_usage(self.disk_path)
        except Exception:
            disk = psutil.disk_usage("/")

        disk_usage = disk.percent

        process_count = len(psutil.pids())

        metrics = {
            # Keep displayed CPU aligned with Task Manager-like instantaneous reading.
            "cpu_usage": round(cpu_usage, 2),
            "cpu_usage_raw": round(cpu_usage, 2),
            "cpu_usage_smoothed": round(cpu_smoothed, 2),
            "memory_usage": round(memory_usage, 2),
            "disk_usage": round(disk_usage, 2),
            "process_count": process_count,
            "process_count_threshold": self.process_count_threshold,
            "memory_used_gb": round(vm.used / (1024 ** 3), 2),
            "memory_total_gb": round(vm.total / (1024 ** 3), 2),
            "disk_used_gb": round(disk.used / (1024 ** 3), 2),
            "disk_total_gb": round(disk.total / (1024 ** 3), 2),
            "sampled_at": sampled_at,
        }

        anomaly = self._update_baselines(metrics)
        metrics["anomaly"] = anomaly
        metrics["risk_level"] = self._risk_level(metrics, anomaly)
        return metrics

    def detect_events(self, metrics):
        detected_events = []
        anomaly = metrics.get("anomaly", {})

        if metrics["cpu_usage"] >= self.cpu_threshold or anomaly.get("cpu_usage", {}).get("is_spike"):
            detected_events.append("CPU_SPIKE")
        if metrics["memory_usage"] >= self.memory_threshold or anomaly.get("memory_usage", {}).get("is_spike"):
            detected_events.append("MEMORY_SPIKE")
        if metrics["disk_usage"] >= self.disk_threshold or anomaly.get("disk_usage", {}).get("is_spike"):
            detected_events.append("DISK_SPIKE")
        if metrics["process_count"] >= self.process_count_threshold or anomaly.get("process_count", {}).get("is_spike"):
            detected_events.append("HIGH_PROCESS_COUNT")

        if not detected_events:
            detected_events.append("NORMAL")

        primary_event = detected_events[0]

        return primary_event, detected_events