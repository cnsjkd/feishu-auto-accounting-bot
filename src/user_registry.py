"""User binding and identity helpers for multi-user accounting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from commands import BitableLink, parse_bitable_link
from db import AccountingDB, UserBinding

if TYPE_CHECKING:
    from feishu_client import FeishuClient


@dataclass(frozen=True)
class FeishuUserIdentity:
    tenant_key: str
    open_id: str
    union_id: str = ""
    user_name: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.open_id)


class UserRegistry:
    def __init__(self, db: AccountingDB, feishu_client: "FeishuClient"):
        self.db = db
        self.feishu_client = feishu_client

    def bind_from_link(self, identity: FeishuUserIdentity, raw_link: str) -> UserBinding:
        if not identity.is_valid:
            raise ValueError("无法识别飞书用户身份，不能绑定账本。")
        parsed = parse_bitable_link(raw_link)
        app_token = self.resolve_app_token(parsed)
        binding = self.db.upsert_user_binding(
            tenant_key=identity.tenant_key or "default",
            open_id=identity.open_id,
            union_id=identity.union_id,
            user_name=identity.user_name,
            bitable_app_token=app_token,
            default_table_id=parsed.table_id,
            bitable_view_url=parsed.view_url,
        )
        return binding

    def resolve_app_token(self, parsed: BitableLink) -> str:
        if parsed.app_token:
            return parsed.app_token
        if parsed.wiki_token:
            return self.feishu_client.resolve_wiki_obj_token(parsed.wiki_token)
        raise ValueError("无法从链接中解析 Bitable app token。")

    def get_binding(self, identity: FeishuUserIdentity) -> UserBinding | None:
        if not identity.is_valid:
            return None
        return self.db.get_user_binding(identity.tenant_key or "default", identity.open_id)


def extract_user_identity(payload: dict[str, Any]) -> FeishuUserIdentity:
    """Extract tenant/open_id from Feishu event payload."""
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}

    tenant_key = str(header.get("tenant_key") or event.get("tenant_key") or "default")
    open_id = str(
        sender_id.get("open_id")
        or sender.get("open_id")
        or event.get("open_id")
        or event.get("user_open_id")
        or ""
    )
    union_id = str(sender_id.get("union_id") or sender.get("union_id") or event.get("union_id") or "")
    user_name = str(sender.get("sender_name") or sender.get("name") or event.get("user_name") or "")
    return FeishuUserIdentity(tenant_key=tenant_key, open_id=open_id, union_id=union_id, user_name=user_name)
