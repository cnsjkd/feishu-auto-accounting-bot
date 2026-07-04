"""Lightweight background scheduler using only the Python standard library."""

from __future__ import annotations

import threading
import time
from datetime import date, datetime

from monthly_summary import MonthlySummaryService


class MonthlySummaryScheduler:
    """Run previous-month summaries on the first day of each month."""

    def __init__(self, service: MonthlySummaryService, run_hour: int = 9):
        self.service = service
        self.run_hour = run_hour
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_key = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="monthly-summary-scheduler", daemon=True)
        self._thread.start()
        print("[INFO] 月报定时任务已启动：每月 1 日自动总结上个月收支", flush=True)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._run_if_due()
            self._stop_event.wait(300)

    def _run_if_due(self) -> None:
        now = datetime.now()
        today = date.today()
        run_key = today.strftime("%Y-%m-%d")
        if today.day != 1 or now.hour < self.run_hour or self._last_run_key == run_key:
            return
        try:
            summaries = self.service.run_for_previous_month(send_message=True, force=False)
            print(f"[INFO] 月报定时任务完成: sent={len(summaries)}", flush=True)
            self._last_run_key = run_key
        except Exception as exc:  # noqa: BLE001 - scheduler must not kill the service
            print(f"[ERROR] 月报定时任务失败: {exc}", flush=True)
