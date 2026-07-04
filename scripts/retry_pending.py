"""重试写入本地 pending_bills.jsonl 队列中的账单。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import get_settings  # noqa: E402
from feishu_client import FeishuClient  # noqa: E402
from models import Bill  # noqa: E402

QUEUE_PATH = ROOT / "data" / "pending_bills.jsonl"
FAILED_PATH = ROOT / "data" / "pending_bills.failed.jsonl"


def bill_from_fields(fields: dict) -> Bill:
    return Bill(
        date=fields.get("日期", ""),
        time=fields.get("时间", ""),
        bill_type=fields.get("类型", "支出"),
        amount=float(fields.get("金额", 0)),
        currency=fields.get("币种", "CNY"),
        category=fields.get("分类", "其他"),
        payment_method=fields.get("支付方式", "其他"),
        merchant=fields.get("商户或对象", ""),
        note=fields.get("备注", ""),
        original_text=fields.get("原始文本", ""),
        source=fields.get("记录来源", "本地重试"),
        dedupe_id=fields.get("唯一去重 ID", ""),
        created_at=fields.get("创建时间", ""),
    )


def main() -> int:
    if not QUEUE_PATH.exists():
        print("没有待重试账单")
        return 0

    settings = get_settings()
    client = FeishuClient(settings)
    lines = QUEUE_PATH.read_text(encoding="utf-8").splitlines()
    failed: list[str] = []
    success_count = 0

    for line in lines:
        if not line.strip():
            continue
        payload = json.loads(line)
        bill = bill_from_fields(payload["bill"])
        try:
            created, _ = client.save_bill_once(bill)
            if created:
                success_count += 1
                print(f"[OK] 已补写: {bill.dedupe_id}")
            else:
                print(f"[SKIP] 已存在，跳过: {bill.dedupe_id}")
        except Exception as exc:  # noqa: BLE001 - 保留失败项
            payload["retry_error"] = str(exc)
            failed.append(json.dumps(payload, ensure_ascii=False))
            print(f"[ERROR] 补写失败: {bill.dedupe_id} -> {exc}")

    if failed:
        FAILED_PATH.write_text("\n".join(failed) + "\n", encoding="utf-8")
        QUEUE_PATH.write_text("\n".join(failed) + "\n", encoding="utf-8")
        print(f"完成：成功 {success_count} 条，失败 {len(failed)} 条，失败项仍保留在 {QUEUE_PATH}")
    else:
        QUEUE_PATH.unlink()
        print(f"完成：成功/跳过 {len(lines)} 条，待重试队列已清空")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
