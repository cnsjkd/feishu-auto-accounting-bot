"""本地轻量测试：不调用外部 API，只验证标准化、去重 ID 和字段映射。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from models import Bill  # noqa: E402
from utils import build_dedupe_id, normalize_bill_data  # noqa: E402


def main() -> int:
    raw = {
        "日期": "2026-07-02",
        "时间": "12:30",
        "类型": "支出",
        "金额": "38.5",
        "币种": "cny",
        "分类": "餐饮",
        "支付方式": "其他",
        "商户或对象": "美团外卖",
        "备注": "午餐",
    }
    normalized = normalize_bill_data(raw, "今天中午美团点外卖花了38.5", "测试")
    dedupe_id = build_dedupe_id(normalized)
    bill = Bill(
        date=normalized["日期"],
        time=normalized["时间"],
        bill_type=normalized["类型"],
        amount=normalized["金额"],
        currency=normalized["币种"],
        category=normalized["分类"],
        payment_method=normalized["支付方式"],
        merchant=normalized["商户或对象"],
        note=normalized["备注"],
        original_text=normalized["原始文本"],
        source=normalized["记录来源"],
        dedupe_id=dedupe_id,
    )
    fields = bill.to_bitable_fields()
    assert fields["金额"] == 38.5
    assert fields["时间"] == "12:30:00"
    assert fields["唯一去重 ID"] == dedupe_id
    print("本地轻量测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
