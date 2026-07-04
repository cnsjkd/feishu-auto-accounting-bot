"""测试飞书文本消息事件解析。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from feishu_event import extract_message_info, extract_message_text  # noqa: E402
from utils import build_external_dedupe_id  # noqa: E402


def main() -> int:
    payload = {
        "event": {
            "message": {
                "message_id": "om_xxx",
                "message_type": "text",
                "content": "{\"text\":\"今天中午美团点外卖花了38.5\"}",
            }
        }
    }
    text, source = extract_message_text(payload)
    assert text == "今天中午美团点外卖花了38.5"
    assert source == "飞书机器人:om_xxx"
    text, source, message_id = extract_message_info(payload)
    assert text == "今天中午美团点外卖花了38.5"
    assert source == "飞书机器人:om_xxx"
    assert message_id == "om_xxx"
    assert build_external_dedupe_id("feishu_message", message_id) == build_external_dedupe_id(
        "feishu_message", "om_xxx"
    )

    at_payload = {
        "event": {
            "message": {
                "message_id": "om_at",
                "message_type": "text",
                "content": "{\"text\":\"<at user_id=\\\"ou_xxx\\\">自动记账小助手</at> 今天买菜花了30元\"}",
            }
        }
    }
    text, source = extract_message_text(at_payload)
    assert text == "今天买菜花了30元"
    assert source == "飞书机器人:om_at"

    legacy_payload = {
        "event": {
            "uuid": "legacy_xxx",
            "msg_type": "text",
            "text": "@_user_1 今日我的工资到账3500万元",
        }
    }
    text, source = extract_message_text(legacy_payload)
    assert text == "@_user_1 今日我的工资到账3500万元"
    assert source == "飞书机器人旧版事件:legacy_xxx"
    print("飞书事件解析测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
