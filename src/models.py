"""账单领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils import beijing_now_iso


@dataclass
class Bill:
    date: str
    time: str
    bill_type: str
    amount: float
    currency: str
    category: str
    payment_method: str
    merchant: str
    note: str
    original_text: str
    source: str
    dedupe_id: str
    created_at: str = field(default_factory=beijing_now_iso)

    def to_bitable_fields(self) -> dict[str, Any]:
        """转换为飞书多维表格字段名到字段值的映射。"""
        return {
            "日期": self.date,
            "时间": self.time,
            "类型": self.bill_type,
            "金额": self.amount,
            "币种": self.currency,
            "分类": self.category,
            "支付方式": self.payment_method,
            "商户或对象": self.merchant,
            "备注": self.note,
            "原始文本": self.original_text,
            "记录来源": self.source,
            "创建时间": self.created_at,
            "唯一去重 ID": self.dedupe_id,
        }
