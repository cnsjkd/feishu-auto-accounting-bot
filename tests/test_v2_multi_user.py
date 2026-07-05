"""V2 tests: commands, user isolation, monthly tables and summaries."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from commands import build_table_view_url, parse_bitable_link, parse_command  # noqa: E402
from config import LLMConfig, Settings  # noqa: E402
from db import AccountingDB  # noqa: E402
from models import Bill  # noqa: E402
from monthly_summary import build_summary, previous_month_key  # noqa: E402
from monthly_table_manager import MonthlyTableManager, month_key_from_date  # noqa: E402
from utils import beijing_now_iso, today_context  # noqa: E402
from service import AccountingService  # noqa: E402
from user_registry import FeishuUserIdentity, UserRegistry, extract_user_identity  # noqa: E402


@dataclass
class FakeParser:
    responses: list[list[Bill]]

    def parse(self, text: str, source: str = "飞书机器人", dedupe_id: str = "") -> Bill:
        return self.parse_many(text, source=source, dedupe_id=dedupe_id)[0]

    def parse_many(self, text: str, source: str = "飞书机器人", dedupe_id: str = "") -> list[Bill]:
        bills = self.responses.pop(0)
        for index, bill in enumerate(bills, start=1):
            bill.source = source
            bill.dedupe_id = f"{dedupe_id}:{index}" if dedupe_id else bill.dedupe_id
        return bills


class FakeFeishuClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.tables_by_app: dict[str, dict[str, str]] = {}
        self.fields: dict[tuple[str, str], set[str]] = {}
        self.records: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.sent_messages: list[tuple[str, str]] = []

    def resolve_wiki_obj_token(self, wiki_token: str) -> str:
        return "base_from_" + wiki_token

    def list_tables(self, app_token: str) -> list[dict[str, Any]]:
        return [
            {"table_id": table_id, "name": name}
            for name, table_id in self.tables_by_app.get(app_token, {}).items()
        ]

    def create_table(self, app_token: str, table_name: str) -> str:
        tables = self.tables_by_app.setdefault(app_token, {})
        table_id = f"tbl_{app_token}_{table_name}".replace("-", "_")
        tables[table_name] = table_id
        return table_id

    def ensure_bitable_fields(self, app_token: str, table_id: str) -> None:
        self.fields[(app_token, table_id)] = {"日期", "时间", "类型", "金额", "币种", "分类", "支付方式", "商户或对象", "备注", "原始文本", "记录来源", "创建时间", "唯一去重 ID"}

    def save_bill_once(self, bill: Bill, app_token: str = "", table_id: str = "") -> tuple[bool, dict[str, Any] | None]:
        key = (app_token, table_id)
        records = self.records.setdefault(key, [])
        if any(item["fields"].get("唯一去重 ID") == bill.dedupe_id for item in records):
            return False, None
        records.append({"fields": bill.to_bitable_fields()})
        return True, {"code": 0}

    def list_records(self, app_token: str, table_id: str, page_size: int = 500) -> list[dict[str, Any]]:
        return list(self.records.get((app_token, table_id), []))

    def send_text_to_open_id(self, open_id: str, text: str) -> dict[str, Any]:
        self.sent_messages.append((open_id, text))
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
        server_port=18000,
    )


def make_bill(
    date: str,
    bill_type: str,
    amount: float,
    dedupe_id: str,
    *,
    category: str | None = None,
    merchant: str = "测试商户",
    note: str = "测试",
    original_text: str = "测试账单",
) -> Bill:
    return Bill(
        date=date,
        time="12:00:00",
        bill_type=bill_type,
        amount=amount,
        currency="CNY",
        category=category or ("餐饮" if bill_type == "支出" else "工资"),
        payment_method="其他",
        merchant=merchant,
        note=note,
        original_text=original_text,
        source="飞书机器人",
        dedupe_id=dedupe_id,
    )


def main() -> int:
    assert parse_command("帮助").name == "help"
    bind_cmd = parse_command("绑定账本 https://x.feishu.cn/base/basea?table=tbla&view=v")
    assert bind_cmd and bind_cmd.name == "bind"
    parsed = parse_bitable_link(bind_cmd.argument)
    assert parsed.app_token == "basea"
    assert parsed.table_id == "tbla"
    wiki = parse_bitable_link("https://x.feishu.cn/wiki/wikixxx?table=tblw&view=v")
    assert wiki.wiki_token == "wikixxx"
    assert (
        build_table_view_url("https://x.feishu.cn/base/basea?table=tbla&view=v", "tbl_2026_07")
        == "https://x.feishu.cn/base/basea?table=tbl_2026_07"
    )

    payload = {
        "header": {"tenant_key": "tenant_a"},
        "event": {"sender": {"sender_id": {"open_id": "ou_a", "union_id": "on_a"}, "sender_name": "A"}},
    }
    identity = extract_user_identity(payload)
    assert identity.tenant_key == "tenant_a"
    assert identity.open_id == "ou_a"

    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings()
        db = AccountingDB(Path(tmp) / "accounting.db")
        fake_client = FakeFeishuClient(settings)
        registry = UserRegistry(db, fake_client)  # type: ignore[arg-type]
        monthly_manager = MonthlyTableManager(db, fake_client)  # type: ignore[arg-type]
        parser = FakeParser(
            [
                [make_bill("2026-07-04", "支出", 28.0, "dedupe_a")],
                [make_bill("2026-07-04", "收入", 100.0, "dedupe_b")],
                [make_bill("2026-07-04", "支出", 28.0, "dedupe_a_again")],
                [
                    make_bill("2026-07-04", "支出", 3.0, "multi_1", category="餐饮", merchant="米饭", original_text="米饭3元"),
                    make_bill("2026-07-04", "支出", 15.0, "multi_2", category="烟酒", merchant="烟", original_text="烟15元"),
                    make_bill("2026-07-04", "支出", 286.0, "multi_3", category="投资", merchant="基金", note="亏损", original_text="基金亏损286元"),
                ],
            ]
        )
        service = AccountingService(
            parser=parser,  # type: ignore[arg-type]
            feishu_client=fake_client,  # type: ignore[arg-type]
            db=db,
            user_registry=registry,
            monthly_table_manager=monthly_manager,
        )

        user_a = FeishuUserIdentity("tenant", "ou_a", user_name="A")
        user_b = FeishuUserIdentity("tenant", "ou_b", user_name="B")
        bind_a = service.handle_user_text(
            "绑定账本 https://x.feishu.cn/base/base_a?table=tbl_seed_a&view=v",
            identity=user_a,
            source="飞书机器人:om_bind_a",
        )
        bind_b = service.handle_user_text(
            "绑定账本 https://x.feishu.cn/base/base_b?table=tbl_seed_b&view=v",
            identity=user_b,
            source="飞书机器人:om_bind_b",
        )
        assert bind_a["command"] == "bind"
        assert bind_b["command"] == "bind"
        assert "重新发送" in bind_a["reply"]
        rebind_a = service.handle_user_text(
            "绑定账本 https://x.feishu.cn/base/base_a_new?table=tbl_seed_new&view=v",
            identity=user_a,
            source="飞书机器人:om_rebind_a",
        )
        assert rebind_a["rebound"] is True
        assert "重新绑定成功" in rebind_a["reply"]
        service.handle_user_text(
            "绑定账本 https://x.feishu.cn/base/base_a?table=tbl_seed_a&view=v",
            identity=user_a,
            source="飞书机器人:om_bind_a_back",
        )

        result_a = service.handle_user_text("今天午饭28", identity=user_a, source="飞书机器人:om_a", dedupe_id="msg_a")
        result_b = service.handle_user_text("今天工资100", identity=user_b, source="飞书机器人:om_b", dedupe_id="msg_b")
        assert result_a["created"] is True
        assert result_b["created"] is True
        assert result_a["month_key"] == "2026-07"
        assert result_b["month_key"] == "2026-07"
        assert result_a["table_url"] == f"https://x.feishu.cn/base/base_a?table={result_a['table_id']}"
        assert result_b["table_url"] == f"https://x.feishu.cn/base/base_b?table={result_b['table_id']}"
        assert "tbl_seed" not in result_a["table_url"]
        assert "view=" not in result_a["table_url"]
        assert result_a["table_id"] != result_b["table_id"]
        assert ("base_a", result_a["table_id"]) in fake_client.records
        assert ("base_b", result_b["table_id"]) in fake_client.records

        duplicate = service.handle_user_text("今天午饭28", identity=user_a, source="飞书机器人:om_a", dedupe_id="msg_a")
        assert duplicate["created"] is False

        multi = service.handle_user_text(
            "今天吃了米饭3元，烟15，基金亏损286",
            identity=user_a,
            source="飞书机器人:om_multi",
            dedupe_id="msg_multi",
        )
        assert multi["count"] == 3
        assert multi["created_count"] == 3
        assert multi["queued_count"] == 0
        records_a = fake_client.records[("base_a", result_a["table_id"])]
        assert len(records_a) == 4
        multi_fields = [record["fields"] for record in records_a[-3:]]
        assert [fields["金额"] for fields in multi_fields] == [3.0, 15.0, 286.0]
        assert [fields["分类"] for fields in multi_fields] == ["餐饮", "烟酒", "投资"]
        assert [fields["商户或对象"] for fields in multi_fields] == ["米饭", "烟", "基金"]

        assert month_key_from_date("2026-07-04") == "2026-07"
        assert previous_month_key(__import__("datetime").date(2026, 8, 1)) == "2026-07"
        assert beijing_now_iso().endswith("+08:00")
        assert today_context()["timezone"] == "Asia/Shanghai"

        summary = build_summary("2026-07", fake_client.records[("base_a", result_a["table_id"])])
        assert summary.expense == 332.0
        assert summary.income == 0.0
        assert "2026-07 月度记账总结" in summary.text
        assert "支出：¥332.00" in summary.text

    print("V2 多用户隔离、月度表和月报测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
