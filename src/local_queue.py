"""本地待重试队列。

当 Bitable 暂时不可写时，把账单保存到 JSONL 文件，避免数据丢失。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import PROJECT_ROOT
from utils import beijing_now_iso, json_dumps


QUEUE_PATH = PROJECT_ROOT / "data" / "pending_bills.jsonl"


def append_pending_bill(bill_fields: dict[str, Any], error: str) -> Path:
    """把写入失败的账单追加到本地队列。"""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "queued_at": beijing_now_iso(),
        "error": error,
        "bill": bill_fields,
    }
    with QUEUE_PATH.open("a", encoding="utf-8") as file:
        file.write(json_dumps(payload) + "\n")
    return QUEUE_PATH
