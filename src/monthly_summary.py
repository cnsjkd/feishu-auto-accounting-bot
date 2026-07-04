"""Monthly accounting summary generation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from db import AccountingDB, UserBinding
from utils import beijing_today

if TYPE_CHECKING:
    from feishu_client import FeishuClient


@dataclass(frozen=True)
class MonthlySummary:
    month_key: str
    income: float
    expense: float
    balance: float
    count: int
    category_expense: dict[str, float]
    max_expense: dict[str, Any] | None
    text: str


class MonthlySummaryService:
    def __init__(self, db: AccountingDB, feishu_client: "FeishuClient"):
        self.db = db
        self.feishu_client = feishu_client

    def run_for_previous_month(self, *, send_message: bool = True, force: bool = False) -> list[MonthlySummary]:
        return self.run_for_month(previous_month_key(), send_message=send_message, force=force)

    def run_for_month(self, month_key: str, *, send_message: bool = True, force: bool = False) -> list[MonthlySummary]:
        summaries: list[MonthlySummary] = []
        for user in self.db.list_active_users():
            if self.db.has_monthly_summary(user.id, month_key) and not force:
                continue
            monthly_table = self.db.get_monthly_table(user.id, month_key)
            if not monthly_table:
                continue
            records = self.feishu_client.list_records(user.bitable_app_token, monthly_table.table_id)
            summary = build_summary(month_key, records, user)
            self.db.save_monthly_summary(user.id, month_key, summary.text)
            if send_message:
                self.feishu_client.send_text_to_open_id(user.open_id, summary.text)
            summaries.append(summary)
        return summaries


def build_summary(month_key: str, records: list[dict[str, Any]], user: UserBinding | None = None) -> MonthlySummary:
    income = 0.0
    expense = 0.0
    count = 0
    category_expense: dict[str, float] = defaultdict(float)
    max_expense: dict[str, Any] | None = None

    for record in records:
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else record
        if not isinstance(fields, dict):
            continue
        bill_type = str(fields.get("类型") or "支出")
        amount = _to_float(fields.get("金额"))
        if amount <= 0:
            continue
        count += 1
        if bill_type == "收入":
            income += amount
        else:
            expense += amount
            category = str(fields.get("分类") or "其他")
            category_expense[category] += amount
            if max_expense is None or amount > _to_float(max_expense.get("金额")):
                max_expense = {
                    "金额": amount,
                    "分类": category,
                    "商户或对象": fields.get("商户或对象", ""),
                    "备注": fields.get("备注", ""),
                    "日期": fields.get("日期", ""),
                }

    income = round(income, 2)
    expense = round(expense, 2)
    balance = round(income - expense, 2)
    category_sorted = dict(sorted(category_expense.items(), key=lambda item: item[1], reverse=True))
    text = format_summary_text(
        month_key=month_key,
        income=income,
        expense=expense,
        balance=balance,
        count=count,
        category_expense=category_sorted,
        max_expense=max_expense,
        table_url=user.bitable_view_url if user else "",
    )
    return MonthlySummary(
        month_key=month_key,
        income=income,
        expense=expense,
        balance=balance,
        count=count,
        category_expense=category_sorted,
        max_expense=max_expense,
        text=text,
    )


def format_summary_text(
    *,
    month_key: str,
    income: float,
    expense: float,
    balance: float,
    count: int,
    category_expense: dict[str, float],
    max_expense: dict[str, Any] | None,
    table_url: str = "",
) -> str:
    lines = [
        f"{month_key} 月度记账总结",
        "",
        f"收入：¥{income:.2f}",
        f"支出：¥{expense:.2f}",
        f"结余：¥{balance:.2f}",
        f"本月共记录：{count} 笔",
    ]
    if category_expense:
        lines.extend(["", "支出分类："])
        for category, amount in category_expense.items():
            lines.append(f"- {category}：¥{amount:.2f}")
    if max_expense:
        lines.extend(
            [
                "",
                "最大单笔支出："
                f"{max_expense.get('分类', '其他')} ¥{_to_float(max_expense.get('金额')):.2f} "
                f"{max_expense.get('商户或对象') or max_expense.get('备注') or ''}".strip(),
            ]
        )
    if table_url:
        lines.extend(["", f"查看完整账本：{table_url}"])
    return "\n".join(lines)


def previous_month_key(today: date | None = None) -> str:
    current = today or beijing_today()
    first_day = current.replace(day=1)
    previous_day = first_day - timedelta(days=1)
    return previous_day.strftime("%Y-%m")


def _to_float(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0
