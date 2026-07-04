"""HTTP-level tests for Feishu event handling in V2."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import LLMConfig, Settings  # noqa: E402
from db import AccountingDB  # noqa: E402
from feishu_event import run_server  # noqa: E402
from models import Bill  # noqa: E402
from monthly_table_manager import MonthlyTableManager  # noqa: E402
from service import AccountingService  # noqa: E402
from user_registry import UserRegistry  # noqa: E402


@dataclass
class FakeParser:
    bills: list[Bill]

    def parse(self, text: str, source: str = "飞书机器人", dedupe_id: str = "") -> Bill:
        return self.parse_many(text, source=source, dedupe_id=dedupe_id)[0]

    def parse_many(self, text: str, source: str = "飞书机器人", dedupe_id: str = "") -> list[Bill]:
        template = self.bills.pop(0)
        template.source = source
        template.dedupe_id = dedupe_id or template.dedupe_id
        return [template]


class FakeFeishuClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.records: list[dict[str, Any]] = []
        self.replies: list[tuple[str, str]] = []
        self.tables: dict[str, str] = {}

    def resolve_wiki_obj_token(self, wiki_token: str) -> str:
        return "base_from_" + wiki_token

    def list_tables(self, app_token: str) -> list[dict[str, Any]]:
        return [{"name": name, "table_id": table_id} for name, table_id in self.tables.items()]

    def create_table(self, app_token: str, table_name: str) -> str:
        table_id = "tbl_" + table_name.replace("-", "_")
        self.tables[table_name] = table_id
        return table_id

    def ensure_bitable_fields(self, app_token: str, table_id: str) -> None:
        return None

    def save_bill_once(self, bill: Bill, app_token: str = "", table_id: str = "") -> tuple[bool, dict[str, Any] | None]:
        if any(item["fields"].get("唯一去重 ID") == bill.dedupe_id for item in self.records):
            return False, None
        self.records.append({"app_token": app_token, "table_id": table_id, "fields": bill.to_bitable_fields()})
        return True, {"code": 0}

    def reply_message(self, message_id: str, text: str) -> dict[str, Any]:
        self.replies.append((message_id, text))
        return {"code": 0}


def make_settings() -> Settings:
    return Settings(
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
        bitable_app_token="base_default",
        table_id="tbl_default",
        bitable_view_url="https://example.feishu.cn/base/base_default?table=tbl_default",
        llm_provider="qwen",
        llm_configs={"qwen": LLMConfig("qwen", "sk", "https://example.com/v1", "qwen")},
        server_host="127.0.0.1",
        server_port=18181,
    )


def make_bill() -> Bill:
    return Bill(
        date="2026-07-04",
        time="18:00:00",
        bill_type="支出",
        amount=18.0,
        currency="CNY",
        category="餐饮",
        payment_method="其他",
        merchant="测试餐厅",
        note="",
        original_text="晚饭18元",
        source="飞书机器人",
        dedupe_id="placeholder",
    )


def post_json(port: int, payload: dict[str, Any]) -> dict[str, Any]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode("utf-8")
    conn.request("POST", "/feishu/events", body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    raw = response.read().decode("utf-8")
    conn.close()
    assert response.status == 200, raw
    return json.loads(raw)


def message_payload(message_id: str, text: str, open_id: str = "ou_http") -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {"tenant_key": "tenant_http", "event_id": "evt_" + message_id},
        "event": {
            "sender": {"sender_id": {"open_id": open_id, "union_id": "on_http"}, "sender_name": "HTTP User"},
            "message": {
                "message_id": message_id,
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings()
        db = AccountingDB(Path(tmp) / "accounting.db")
        client = FakeFeishuClient(settings)
        service = AccountingService(
            parser=FakeParser([make_bill()]),  # type: ignore[arg-type]
            feishu_client=client,  # type: ignore[arg-type]
            db=db,
            user_registry=UserRegistry(db, client),  # type: ignore[arg-type]
            monthly_table_manager=MonthlyTableManager(db, client),  # type: ignore[arg-type]
        )
        thread = Thread(target=run_server, args=("127.0.0.1", settings.server_port, service), daemon=True)
        thread.start()

        unbound = post_json(settings.server_port, message_payload("om_unbound", "晚饭18元"))
        assert unbound["ok"] is True
        assert client.records == []
        assert len(client.replies) == 1
        assert "绑定个人账本" in client.replies[-1][1]

        bind = post_json(
            settings.server_port,
            message_payload("om_bind", "绑定账本 https://x.feishu.cn/base/base_http?table=tbl_seed&view=v"),
        )
        assert bind["ok"] is True
        assert len(client.replies) == 2
        assert "账本绑定成功" in client.replies[-1][1]

        created = post_json(settings.server_port, message_payload("om_bill", "晚饭18元"))
        assert created["ok"] is True
        assert created["created"] is True
        assert len(client.records) == 1
        assert len(client.replies) == 3
        assert "记账成功" in client.replies[-1][1]

        duplicate = post_json(settings.server_port, message_payload("om_bill", "晚饭18元"))
        assert duplicate["ok"] is True
        assert duplicate["ignored"] is True
        assert duplicate["reason"] == "duplicate_persisted"
        assert len(client.records) == 1
        assert len(client.replies) == 3

    print("V2 HTTP 事件、绑定回复和重复投递测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
