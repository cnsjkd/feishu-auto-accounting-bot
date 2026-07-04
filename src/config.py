"""配置读取模块。

只使用标准库读取 .env，避免引入 python-dotenv 等额外依赖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class LLMConfig:
    name: str
    api_key: str
    api_base: str
    model: str

    @property
    def chat_completions_url(self) -> str:
        return self.api_base.rstrip("/") + "/chat/completions"


def load_env_file(path: Path = ENV_PATH) -> None:
    """读取 .env 文件并写入 os.environ。

    已存在的环境变量优先级更高，不会被 .env 覆盖。
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    feishu_app_id: str
    feishu_app_secret: str
    bitable_app_token: str
    table_id: str
    llm_provider: str
    llm_configs: dict[str, LLMConfig]
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    request_timeout: int = 30
    llm_timeout: int = 60
    llm_max_retries: int = 2

    def get_llm_sequence(self) -> list[LLMConfig]:
        """根据 LLM_PROVIDER 返回模型调用顺序。"""
        if self.llm_provider == "fallback":
            sequence = [
                self.llm_configs[name]
                for name in ("qwen", "openai", "legacy")
                if name in self.llm_configs
            ]
            if sequence:
                return sequence
        if self.llm_provider in self.llm_configs:
            return [self.llm_configs[self.llm_provider]]
        if "qwen" in self.llm_configs:
            return [self.llm_configs["qwen"]]
        if "legacy" in self.llm_configs:
            return [self.llm_configs["legacy"]]
        return list(self.llm_configs.values())


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _build_llm_configs() -> dict[str, LLMConfig]:
    """读取新旧两套模型配置。"""
    configs: dict[str, LLMConfig] = {}

    if _env("QWEN_API_KEY"):
        configs["qwen"] = LLMConfig(
            name="qwen",
            api_key=_env("QWEN_API_KEY"),
            api_base=_env("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=_env("QWEN_MODEL", "qwen3.7-plus"),
        )

    if _env("OPENAI_API_KEY"):
        configs["openai"] = LLMConfig(
            name="openai",
            api_key=_env("OPENAI_API_KEY"),
            api_base=_env("OPENAI_API_BASE", "https://api.openai.com/v1"),
            model=_env("OPENAI_MODEL", "gpt-4o-mini"),
        )

    if _env("GPT_API_KEY"):
        configs["legacy"] = LLMConfig(
            name="legacy",
            api_key=_env("GPT_API_KEY"),
            api_base=_env("GPT_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=_env("GPT_MODEL", "qwen3.7-plus"),
        )

    return configs


def get_settings() -> Settings:
    """加载并校验运行配置。"""
    load_env_file()

    required_keys = [
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "BITABLE_APP_TOKEN",
        "TABLE_ID",
    ]
    missing = [key for key in required_keys if not _env(key)]
    if missing:
        raise RuntimeError(
            "缺少必要环境变量: " + ", ".join(missing) + "。请复制 .env.example 为 .env 并填写。"
        )

    llm_configs = _build_llm_configs()
    if not llm_configs:
        raise RuntimeError(
            "缺少模型配置：请填写 QWEN_API_KEY 或 OPENAI_API_KEY；也兼容旧字段 GPT_API_KEY。"
        )

    return Settings(
        feishu_app_id=os.environ["FEISHU_APP_ID"],
        feishu_app_secret=os.environ["FEISHU_APP_SECRET"],
        bitable_app_token=os.environ["BITABLE_APP_TOKEN"],
        table_id=os.environ["TABLE_ID"],
        llm_provider=_env("LLM_PROVIDER", "fallback").lower(),
        llm_configs=llm_configs,
        server_host=_env("SERVER_HOST", "0.0.0.0"),
        server_port=int(_env("SERVER_PORT", "8000")),
        request_timeout=int(_env("REQUEST_TIMEOUT", "30")),
        llm_timeout=int(_env("LLM_TIMEOUT", "60")),
        llm_max_retries=max(1, int(_env("LLM_MAX_RETRIES", "2"))),
    )
