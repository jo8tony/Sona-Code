"""配置系统：Pydantic v2 模型 + 默认配置工厂 + 加载/保存。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

CONFIG_PATH = os.path.expanduser("~/.sona-code/config.json")
ApiType = Literal["chat_completions", "responses"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8117
    admin_prefix: str = "/__recorder"

    @field_validator("admin_prefix")
    @classmethod
    def _admin_prefix_must_start_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("admin_prefix 必须以 / 开头")
        return v


class UpstreamModelConfig(BaseModel):
    id: str
    display_name: str = ""
    api_key: str = ""
    api_type: ApiType | None = None
    context_length: int | None = Field(default=None, gt=0)
    output_length: int | None = Field(default=None, gt=0)
    reasoning: bool = False
    reasoning_efforts: list[ReasoningEffort] = Field(default_factory=list)
    default_effort: ReasoningEffort | None = None
    tool_call: bool = True
    # text 始终可输入；其余输入模态按模型能力显式开启。
    input_modalities: list[Literal["image", "audio", "video", "pdf"]] = Field(default_factory=list)

    @field_validator("api_key")
    @classmethod
    def _api_key_header_safe(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("API Key 不能包含换行")
        return value.strip()

    @field_validator("id")
    @classmethod
    def _model_id(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("模型 ID 不能为空或包含空白字符")
        return value

    @field_validator("input_modalities")
    @classmethod
    def _unique_modalities(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _check_reasoning(self) -> "UpstreamModelConfig":
        self.reasoning_efforts = list(dict.fromkeys(self.reasoning_efforts))
        if self.reasoning_efforts and not self.reasoning:
            raise ValueError("配置思考强度前必须开启支持思考")
        if self.default_effort and self.default_effort not in self.reasoning_efforts:
            raise ValueError("默认思考强度必须属于支持的档位")
        return self


class UpstreamConfig(BaseModel):
    name: str
    display_name: str = ""
    base_url: str
    api_key: str = ""
    api_type: ApiType = "chat_completions"
    route_through_proxy: bool = True
    # Web 终端 OpenCode 手动模型及其输入能力。
    models: list[UpstreamModelConfig] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # 默认 keep：完全透明透传客户端凭据头；显式配置 replace 才注入上游 key
    key_strategy: Literal["replace", "keep"] = "keep"
    source: Literal["custom", "sona"] = "custom"

    @field_validator("api_key")
    @classmethod
    def _api_key_header_safe(cls, value: str) -> str:
        return UpstreamModelConfig._api_key_header_safe(value)

    @field_validator("base_url")
    @classmethod
    def _base_url_scheme(cls, v: str) -> str:
        parsed = urlsplit(v)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url 必须是有效的 http/https 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url 不支持内嵌凭据、查询参数或片段")
        return v

    @model_validator(mode="after")
    def _check_models(self) -> "UpstreamConfig":
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("同一上游的模型 ID 不能重复")
        return self


class OutboundConfig(BaseModel):
    proxy_url: str = ""

    @field_validator("proxy_url")
    @classmethod
    def _proxy_url_scheme(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://", "socks5://")):
            raise ValueError("proxy_url 仅支持 http/https/socks5")
        return v


class RecordingConfig(BaseModel):
    dir: str = "~/.sona-code/records"
    redact: bool = True
    redact_headers: list[str] = Field(
        default_factory=lambda: ["authorization", "x-api-key", "api-key", "cookie"]
    )
    # 会话归属头（按优先级，大小写不敏感）：命中非空值即按头值聚合轨迹，
    # 优先于内容哈希（模型+system+首条user）。留空列表 = 仅按内容聚合。
    session_id_headers: list[str] = Field(
        default_factory=lambda: ["x-deepseek-harness-session-id", "x-session-id"]
    )
    record_request_headers: bool = True
    record_response_headers: bool = True
    record_raw_chunks: bool = False
    max_capture_mb: float = 20
    # 保留策略：删除早于 N 天的记录日期（0 = 永久保留）。启动时与每小时执行一次。
    retention_days: int = Field(0, ge=0, le=36500)


class TerminalConfig(BaseModel):
    """Web 终端模块：浏览器中管理 opencode / shell 会话。"""

    enabled: bool = True
    # auto = Windows 优先随包二进制，其他情况回退 PATH；custom = 仅使用 command。
    command_mode: Literal["auto", "custom"] = "auto"
    # 自定义 opencode 命令（支持完整路径或 PATH 中的命令名）
    command: str = "opencode"
    # 传给 opencode 的额外参数
    args: list[str] = Field(default_factory=list)
    # 通用 shell 命令；空 = 自动探测 pwsh.exe → powershell.exe
    shell_command: str = ""
    # 并发会话上限
    max_sessions: int = Field(8, ge=1, le=64)
    # 每会话环形回放缓冲（KB，重连时回放给浏览器）
    scrollback_kb: int = Field(256, ge=0, le=8192)
    # 启动 opencode 时注入代理环境变量，使 LLM 请求经本代理（进入轨迹记录）
    route_through_proxy: bool = True
    # 走哪个命名上游；空 = 默认上游
    proxy_upstream: str = ""
    # OpenCode provider ID；空值时使用所选上游名称。
    opencode_provider: str = ""
    # 注入进程的额外环境变量（可覆盖代理/目录注入，但不可开启 OpenCode 自更新）
    inject_env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_command_mode(cls, value: Any) -> Any:
        """兼容旧配置：默认 opencode 表示自动解析，非默认命令保留为自定义。"""
        if isinstance(value, dict) and "command_mode" not in value:
            migrated = dict(value)
            command = str(migrated.get("command") or "").strip()
            migrated["command_mode"] = "auto" if command in ("", "opencode") else "custom"
            return migrated
        return value

    @model_validator(mode="after")
    def _check_custom_command(self) -> "TerminalConfig":
        self.command = self.command.strip()
        if self.command_mode == "custom" and not self.command:
            raise ValueError("command_mode=custom 时 command 不能为空")
        return self


class ModelChoice(BaseModel):
    provider: str
    model: str


class ModelSettings(BaseModel):
    default_model: ModelChoice | None = None
    show_native_models: bool = False
    source: Literal["sona", "custom", "native"] = "sona"


class SonaSiteConfig(BaseModel):
    environment: Literal["uat", "prod"] = "prod"
    uat_url: str = "https://sona.paasuat.cmbchina.cn"
    prod_url: str = "https://sona.passoa.cmbchina.cn"

    @field_validator("uat_url", "prod_url")
    @classmethod
    def _frontend_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        parsed = urlsplit(value)
        if (parsed.scheme != "https" and not
                (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})):
            raise ValueError("网站前端地址必须使用 HTTPS（本机开发地址可使用 HTTP）")
        if not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("网站前端地址只能填写域名和可选端口")
        return value

    def active_url(self) -> str:
        return self.uat_url if self.environment == "uat" else self.prod_url


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    upstreams: list[UpstreamConfig]
    default_upstream: str
    outbound: OutboundConfig = Field(default_factory=OutboundConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    model_settings: ModelSettings = Field(default_factory=ModelSettings)
    sona_site: SonaSiteConfig = Field(default_factory=SonaSiteConfig)

    @model_validator(mode="before")
    @classmethod
    def _migrate_provider_routes(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            terminal = value.get("terminal", {})
            if isinstance(terminal, dict):
                route = terminal.get("route_through_proxy", True)
                value["upstreams"] = [
                    {"route_through_proxy": route, **u} if isinstance(u, dict) else u
                    for u in value.get("upstreams", [])
                ]
        return value

    @model_validator(mode="after")
    def _check_upstreams(self) -> "AppConfig":
        names = [u.name for u in self.upstreams]
        if len(names) != len(set(names)):
            raise ValueError("上游名称必须唯一")
        if self.default_upstream not in names and (names or self.default_upstream):
            raise ValueError(f"default_upstream '{self.default_upstream}' 不在 upstreams 名称中")
        choice = self.model_settings.default_model
        if choice and not any(u.name == choice.provider and any(m.id == choice.model for m in u.models)
                              for u in self.upstreams):
            raise ValueError("默认模型不存在，请重新选择")
        return self


def default_config() -> AppConfig:
    """默认配置工厂。"""
    return AppConfig(
        upstreams=[UpstreamConfig(name="deepseek", base_url="https://api.deepseek.com")],
        default_upstream="deepseek",
    )


def save_config(cfg: AppConfig, path: str = CONFIG_PATH) -> None:
    """原子写：先写临时文件再 os.replace，避免半截文件。"""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_config(path: str = CONFIG_PATH) -> AppConfig:
    """加载配置；文件不存在时生成默认配置并写盘后返回。"""
    p = Path(path).expanduser()
    if not p.exists():
        cfg = default_config()
        save_config(cfg, str(p))
        return cfg
    with open(p, "r", encoding="utf-8") as f:
        return AppConfig.model_validate(json.load(f))


def resolved_records_dir(cfg: AppConfig) -> Path:
    """记录目录（展开 ~ 后的绝对路径）。"""
    return Path(cfg.recording.dir).expanduser()
