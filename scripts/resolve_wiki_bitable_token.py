"""将飞书知识库 wiki 多维表格链接转换为 Bitable app_token。

用法：
python scripts/resolve_wiki_bitable_token.py "https://xxx.feishu.cn/wiki/xxx?table=tblxxx"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402
from config import get_settings  # noqa: E402
from feishu_client import FeishuClient  # noqa: E402


def parse_wiki_url(url: str) -> tuple[str, str]:
    """从 wiki 链接提取 wiki token 和 table_id。"""
    parsed = urlparse(url)
    match = re.search(r"/wiki/([^/?#]+)", parsed.path)
    if not match:
        raise ValueError("链接中未找到 /wiki/<token>，请传入飞书 wiki 多维表格链接")
    wiki_token = match.group(1)
    table_id = parse_qs(parsed.query).get("table", [""])[0]
    if not table_id:
        raise ValueError("链接中未找到 table=tbl...，请先点击要写入的表格后复制完整链接")
    return wiki_token, table_id


def resolve_obj_token(wiki_token: str) -> str:
    """调用飞书 wiki get_node 接口获取真实 obj_token。"""
    settings = get_settings()
    client = FeishuClient(settings)
    url = "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node"
    headers = {
        "Authorization": f"Bearer {client.get_tenant_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    params = {"token": wiki_token}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=settings.request_timeout)
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"飞书 wiki get_node 接口返回非 JSON: {exc}; HTTP {response.status_code}; {response.text}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"飞书 wiki get_node 接口 HTTP {response.status_code}: {data}")
    except requests.RequestException as exc:
        raise RuntimeError(f"调用飞书 wiki get_node 接口失败: {exc}") from exc

    if data.get("code") != 0:
        raise RuntimeError(f"飞书 wiki get_node 接口返回错误: {data}")

    node = data.get("data", {}).get("node", {})
    obj_token = node.get("obj_token")
    obj_type = node.get("obj_type")
    if not obj_token:
        raise RuntimeError(f"返回结果中未找到 obj_token: {data}")
    if obj_type and obj_type not in {"bitable", "base"}:
        print(f"[WARN] 当前 wiki 节点类型是 {obj_type}，请确认它确实是多维表格。")
    return obj_token


def update_env(app_token: str, table_id: str) -> None:
    """把解析结果写回项目 .env。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        raise FileNotFoundError("未找到 .env，请先复制 .env.example 为 .env")

    lines = env_path.read_text(encoding="utf-8").splitlines()
    replacements = {
        "BITABLE_APP_TOKEN": app_token,
        "TABLE_ID": table_id,
    }
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            new_lines.append(line)
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in replacements:
            new_lines.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in replacements.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="解析飞书 wiki 多维表格链接中的 Bitable app_token")
    parser.add_argument("wiki_url", help="飞书 wiki 多维表格链接")
    parser.add_argument("--write-env", action="store_true", help="把解析结果写入项目 .env")
    args = parser.parse_args()

    try:
        wiki_token, table_id = parse_wiki_url(args.wiki_url)
        app_token = resolve_obj_token(wiki_token)
        print("解析成功：")
        print(f"BITABLE_APP_TOKEN={app_token}")
        print(f"TABLE_ID={table_id}")
        if args.write_env:
            update_env(app_token, table_id)
            print("已写入 .env")
        return 0
    except Exception as exc:  # noqa: BLE001 - 命令行入口需要清晰报错
        print(f"[ERROR] 解析失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
