"""Monthly Bitable table management."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from db import AccountingDB, MonthlyTable, UserBinding

if TYPE_CHECKING:
    from feishu_client import FeishuClient


class MonthlyTableManager:
    def __init__(self, db: AccountingDB, feishu_client: "FeishuClient"):
        self.db = db
        self.feishu_client = feishu_client

    def get_or_create_for_bill_date(self, user: UserBinding, bill_date: str) -> MonthlyTable:
        month_key = month_key_from_date(bill_date)
        return self.get_or_create(user, month_key)

    def get_or_create(self, user: UserBinding, month_key: str) -> MonthlyTable:
        existing = self.db.get_monthly_table(user.id, month_key)
        if existing:
            self.feishu_client.ensure_bitable_fields(user.bitable_app_token, existing.table_id)
            return existing

        table_name = month_key
        table_id = self._find_table_by_name(user.bitable_app_token, table_name)
        if not table_id:
            table_id = self.feishu_client.create_table(user.bitable_app_token, table_name)
        self.feishu_client.ensure_bitable_fields(user.bitable_app_token, table_id)
        return self.db.upsert_monthly_table(
            user_id=user.id,
            month_key=month_key,
            table_id=table_id,
            table_name=table_name,
        )

    def _find_table_by_name(self, app_token: str, table_name: str) -> str:
        for table in self.feishu_client.list_tables(app_token):
            name = str(table.get("name") or table.get("table_name") or "")
            table_id = str(table.get("table_id") or "")
            if name == table_name and table_id:
                return table_id
        return ""


def month_key_from_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%Y-%m")
    except ValueError:
        return date.today().strftime("%Y-%m")
