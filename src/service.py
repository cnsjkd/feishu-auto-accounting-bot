"""记账业务编排服务。"""

from __future__ import annotations

from typing import Any

from feishu_client import FeishuClient
from gpt_parser import GPTBillParser
from local_queue import append_pending_bill
from models import Bill


class AccountingService:
    def __init__(self, parser: GPTBillParser, feishu_client: FeishuClient):
        self.parser = parser
        self.feishu_client = feishu_client

    def handle_text(self, text: str, source: str = "飞书机器人") -> dict[str, Any]:
        """解析自然语言账单并写入 Bitable。

        如果 Bitable 暂时不可写，保存到本地待重试队列，避免账单丢失。
        """
        bill: Bill = self.parser.parse(text, source=source)
        bill_fields = bill.to_bitable_fields()
        try:
            created, response = self.feishu_client.save_bill_once(bill)
            return {
                "created": created,
                "queued": False,
                "dedupe_id": bill.dedupe_id,
                "bill": bill_fields,
                "feishu_response": response,
            }
        except Exception as exc:  # noqa: BLE001 - 业务层需要兜底落本地队列
            queue_path = append_pending_bill(bill_fields, str(exc))
            print(f"[ERROR] 写入 Bitable 失败，已保存到本地待重试队列: {queue_path}", flush=True)
            return {
                "created": False,
                "queued": True,
                "queue_path": str(queue_path),
                "dedupe_id": bill.dedupe_id,
                "bill": bill_fields,
                "error": str(exc),
            }
