"""诊断当前飞书应用对目标 Bitable 的访问能力。

默认只检查读取字段、读取记录，不产生测试记录或测试字段。
如需测试新增记录权限，显式加参数：--check-record-create
如需测试创建字段权限，显式加参数：--check-field-create
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402
from config import get_settings  # noqa: E402
from feishu_client import FeishuClient  # noqa: E402

TEST_FIELD_NAME = "WorkBuddy权限测试字段"
TEST_TEXT = "WorkBuddy Bitable 写入权限测试，可删除"


def print_result(name: str, ok: bool, detail: str) -> None:
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")


def response_json(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


def is_success(response: requests.Response, data: dict) -> bool:
    return response.status_code < 400 and data.get("code") == 0


def explain_403() -> str:
    return (
        "飞书返回 403/91403。开放平台 API 权限已开通仍 403 时，最常见原因是目标文档没有给应用数据权限。"
        "请检查：1）权限添加后是否已创建新版本、发布并重新安装应用；"
        "2）目标多维表格或所在知识库是否已把该自建应用加入协作者，并授予可编辑权限；"
        "3）如果是 wiki 文档，知识库空间或节点是否限制了应用访问；"
        "4）确认当前 `.env` 的 BITABLE_APP_TOKEN/TABLE_ID 指向的就是你正在授权的那张表。"
    )


def request_and_print(method: str, name: str, url: str, headers: dict, **kwargs) -> tuple[bool, dict]:
    response = requests.request(method, url, headers=headers, **kwargs)
    data = response_json(response)
    ok = is_success(response, data)
    detail = f"HTTP {response.status_code}; 返回={data}"
    if response.status_code == 403 or data.get("code") == 91403:
        detail += "；" + explain_403()
    print_result(name, ok, detail)
    return ok, data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="诊断飞书 Bitable 读写权限")
    parser.add_argument(
        "--check-record-create",
        action="store_true",
        help="额外测试新增记录权限；如果没有删除记录权限，可能残留一条测试记录。",
    )
    parser.add_argument(
        "--check-field-create",
        action="store_true",
        help="额外测试创建字段权限；如果没有删除字段权限，可能残留 WorkBuddy权限测试字段。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    client = FeishuClient(settings)
    token = client.get_tenant_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    base = client.BASE_URL
    app_token = settings.bitable_app_token
    table_id = settings.table_id

    print(f"目标 app_token 后 6 位: {app_token[-6:] if app_token else '<empty>'}")
    print(f"目标 table_id: {table_id}")

    fields_url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100"
    fields_response = requests.get(fields_url, headers=headers, timeout=settings.request_timeout)
    fields_data = response_json(fields_response)
    fields_ok = is_success(fields_response, fields_data)
    fields = fields_data.get("data", {}).get("items", [])
    field_names = [item.get("field_name") for item in fields if item.get("field_name")]
    print_result(
        "读取字段列表",
        fields_ok,
        f"HTTP {fields_response.status_code}; 字段={field_names}; 返回={fields_data}",
    )

    list_records_url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=1"
    list_ok, _ = request_and_print(
        "GET",
        "读取记录列表",
        list_records_url,
        headers,
        timeout=settings.request_timeout,
    )

    record_ok = False
    if args.check_record_create:
        writable_field = "原始文本" if "原始文本" in field_names else (field_names[0] if field_names else "")
        if writable_field:
            records_url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            record_payload = {"fields": {writable_field: TEST_TEXT}}
            record_response = requests.post(
                records_url,
                headers=headers,
                json=record_payload,
                timeout=settings.request_timeout,
            )
            record_data = response_json(record_response)
            record_ok = is_success(record_response, record_data)
            detail = f"HTTP {record_response.status_code}; 使用字段={writable_field}; 返回={record_data}"
            if record_response.status_code == 403 or record_data.get("code") == 91403:
                detail += "；" + explain_403()
            print_result("新增测试记录", record_ok, detail)

            record_id = record_data.get("data", {}).get("record", {}).get("record_id")
            if record_ok and record_id:
                delete_url = f"{records_url}/{record_id}"
                delete_ok, _ = request_and_print(
                    "DELETE",
                    "清理测试记录",
                    delete_url,
                    headers,
                    timeout=settings.request_timeout,
                )
                if not delete_ok:
                    print(f"[WARN] 测试记录未自动删除，请在 Bitable 中手动删除 record_id={record_id}")
        else:
            print_result("新增测试记录", False, "当前表没有任何字段，无法测试写入。")
    else:
        print("[SKIP] 默认跳过新增记录测试；如需测试请加 --check-record-create。")

    if args.check_field_create:
        create_field_url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        field_ok, field_data = request_and_print(
            "POST",
            "创建字段权限",
            create_field_url,
            headers,
            json={"field_name": TEST_FIELD_NAME, "type": 1},
            timeout=settings.request_timeout,
        )

        field_id = field_data.get("data", {}).get("field", {}).get("field_id")
        if field_ok and field_id:
            delete_field_url = f"{create_field_url}/{field_id}"
            delete_field_ok, _ = request_and_print(
                "DELETE",
                "清理测试字段",
                delete_field_url,
                headers,
                timeout=settings.request_timeout,
            )
            if not delete_field_ok:
                print(f"[WARN] 测试字段未自动删除，请在 Bitable 中手动删除字段：{TEST_FIELD_NAME}")
    else:
        print("[SKIP] 默认跳过创建字段权限测试；如需测试请加 --check-field-create。")

    print("\n诊断结论:")
    if args.check_record_create and record_ok:
        print("- 新增记录已通过，可以运行 `python scripts/retry_pending.py` 补写队列。")
    elif fields_ok and list_ok:
        print("- 读取字段和记录正常。若要确认写入权限，请运行 `python scripts/diagnose_bitable_access.py --check-record-create`。")
    elif fields_ok or list_ok:
        print("- 当前应用能读目标表的一部分能力，但权限不完整，请检查应用权限和文档授权。")
    else:
        print("- 当前应用连目标表读取都不稳定，请核对 BITABLE_APP_TOKEN/TABLE_ID 是否对应正在授权的表。")

    return 0 if (fields_ok and list_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
