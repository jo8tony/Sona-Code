"""配置系统测试：默认值 / load 首建 / save-load roundtrip / 非法校验。"""

import pytest
from pydantic import ValidationError

from sona_code.config import (
    AppConfig,
    OutboundConfig,
    RecordingConfig,
    ServerConfig,
    TerminalConfig,
    UpstreamConfig,
    UpstreamModelConfig,
    default_config,
    load_config,
    resolved_records_dir,
    save_config,
)


# ------------------------------------------------------------------ 默认值
def test_default_config_values(tmp_path):
    cfg = default_config()
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8117
    assert cfg.server.admin_prefix == "/__recorder"
    assert [u.name for u in cfg.upstreams] == ["deepseek"]
    assert cfg.upstreams[0].base_url == "https://api.deepseek.com"
    assert cfg.default_upstream == "deepseek"
    assert cfg.outbound.proxy_url == ""
    assert cfg.upstreams[0].key_strategy == "keep"  # 默认透明透传凭据头
    assert cfg.upstreams[0].api_key == ""
    assert cfg.upstreams[0].models == []
    assert cfg.recording.redact is True
    assert cfg.recording.redact_headers == ["authorization", "x-api-key", "api-key", "cookie"]
    assert cfg.recording.max_capture_mb == 20
    assert cfg.terminal.command_mode == "auto"
    assert resolved_records_dir(cfg) != cfg.recording.dir  # 已展开 ~


# ------------------------------------------------------------- load 首建文件
def test_load_creates_default_file(tmp_path):
    p = tmp_path / "config.json"
    assert not p.exists()
    cfg = load_config(str(p))
    assert cfg.default_upstream == "deepseek"
    assert p.exists()
    # 文件内容可再次加载且一致
    assert load_config(str(p)) == cfg
    # 目录中不残留临时文件
    assert list(tmp_path.iterdir()) == [p]


# ------------------------------------------------------- save/load roundtrip
def test_save_load_roundtrip_with_chinese_path(tmp_path):
    p = tmp_path / "配置目录" / "我的配置.json"
    cfg = AppConfig(
        server=ServerConfig(host="0.0.0.0", port=9000, admin_prefix="/__rec"),
        upstreams=[
            UpstreamConfig(
                name="main",
                base_url="http://127.0.0.1:9001",
                api_key="sk-test",
                models=[UpstreamModelConfig(id="vision-local", input_modalities=["image", "pdf"])],
                extra_headers={"X-Org": "组织1"},
            ),
            UpstreamConfig(name="second", base_url="https://api.example.com", key_strategy="keep"),
        ],
        default_upstream="main",
        outbound=OutboundConfig(proxy_url="socks5://127.0.0.1:1080"),
        recording=RecordingConfig(dir=str(tmp_path / "记录目录"), redact=False, max_capture_mb=1.5),
    )
    save_config(cfg, str(p))
    assert p.exists()
    assert load_config(str(p)) == cfg


# ------------------------------------------------------------------- 校验
def test_invalid_empty_upstreams():
    with pytest.raises(ValidationError):
        AppConfig(upstreams=[], default_upstream="main")


def test_invalid_duplicate_upstream_names():
    with pytest.raises(ValidationError):
        AppConfig(
            upstreams=[
                UpstreamConfig(name="a", base_url="http://x.example.com"),
                UpstreamConfig(name="a", base_url="http://y.example.com"),
            ],
            default_upstream="a",
        )


def test_invalid_default_upstream_not_found():
    with pytest.raises(ValidationError):
        AppConfig(
            upstreams=[UpstreamConfig(name="a", base_url="http://x.example.com")],
            default_upstream="nope",
        )


def test_model_ids_and_modalities_validation():
    model = UpstreamModelConfig(id="  local-vision  ", input_modalities=["image", "image", "pdf"])
    assert model.id == "local-vision"
    assert model.input_modalities == ["image", "pdf"]
    with pytest.raises(ValidationError):
        UpstreamModelConfig(id="bad model")
    with pytest.raises(ValidationError):
        UpstreamModelConfig(id="ok", input_modalities=["unknown"])
    with pytest.raises(ValidationError):
        UpstreamConfig(name="a", base_url="https://example.com", models=[model, model])


@pytest.mark.parametrize("base_url", ["ftp://x.example.com", "example.com", ""])
def test_invalid_base_url_scheme(base_url):
    with pytest.raises(ValidationError):
        UpstreamConfig(name="a", base_url=base_url)


@pytest.mark.parametrize("admin_prefix", ["__recorder", "recorder", ""])
def test_invalid_admin_prefix(admin_prefix):
    with pytest.raises(ValidationError):
        ServerConfig(admin_prefix=admin_prefix)


@pytest.mark.parametrize("proxy_url", ["socks4://127.0.0.1:1080", "ftp://x", "127.0.0.1:1080"])
def test_invalid_proxy_url(proxy_url):
    with pytest.raises(ValidationError):
        OutboundConfig(proxy_url=proxy_url)


@pytest.mark.parametrize(
    "proxy_url",
    ["http://127.0.0.1:8080", "https://proxy.example.com", "socks5://127.0.0.1:1080"],
)
def test_valid_proxy_url(proxy_url):
    assert OutboundConfig(proxy_url=proxy_url).proxy_url == proxy_url


def test_empty_proxy_url_valid():
    assert OutboundConfig().proxy_url == ""


def test_terminal_command_mode_migrates_old_config():
    assert TerminalConfig.model_validate({"command": "opencode"}).command_mode == "auto"
    migrated = TerminalConfig.model_validate({"command": r"C:\tools\opencode.exe"})
    assert migrated.command_mode == "custom"
    assert migrated.command == r"C:\tools\opencode.exe"


def test_terminal_custom_command_cannot_be_empty():
    with pytest.raises(ValidationError):
        TerminalConfig(command_mode="custom", command="  ")
