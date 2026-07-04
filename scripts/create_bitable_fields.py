"""可选脚本：按推荐字段在 Bitable 表中创建字段。

如果你已在飞书多维表格中手动建好字段，可以不运行此脚本。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402
from config import get_settings  # noqa: E402
from feishu_client import FeishuClient  # noqa: E402


# Bitable 字段类型可能随飞书版本调整。第一版为了兼容性，文本类字段统一用 1，金额用 2。
FIELDS = [
    {"field_name": "日期", "type": 1},
    {"field_name": "时间", "type": 1},
    {"field_name": "类型", "type": 1},
    {"field_name": "金额", "type": 2},
    {"field_name": "币种", "type": 1},
    {"field_name": "分类", "type": 1},
    {"field_name": "支付方式", "type": 1},
    {"field_name": "商户或对象", "type": 1},
    {"field_name": "备注", "type": 1},
    {"field_name": "原始文本", "type": 1},
    {"field_name": "记录来源", "type": 1},
    {"field_name": "创建时间", "type": 1},
    {"field_name": "唯一去重 ID", "type": 1},
]


def main() -> int:
    settings = get_settings()
    client = FeishuClient(settings)
    url = (
        f"{client.BASE_URL}/bitable/v1/apps/{settings.bitable_app_token}"
        f"/tables/{settings.table_id}/fields"
    )
    headers = {
        "Authorization": f"Bearer {client.get_tenant_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    for field in FIELDS:
        try:
            response = requests.post(url, headers=headers, json=field, timeout=settings.request_timeout)
            try:
                data = response.json()
            except ValueError as exc:
                print(f"[ERROR] 创建字段返回非 JSON {field['field_name']}: HTTP {response.status_code}; {response.text}; {exc}")
                continue
        except requests.RequestException as exc:
            print(f"[ERROR] 创建字段请求失败 {field['field_name']}: {exc}")
            continue

        if response.status_code >= 400:
            print(f"[ERROR] 创建字段失败 {field['field_name']}: HTTP {response.status_code}; {data}")
            continue
        if data.get("code") == 0:
            print(f"[OK] 已创建字段: {field['field_name']}")
        else:
            print(f"[WARN] 字段可能已存在或创建失败: {field['field_name']} -> {data}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
