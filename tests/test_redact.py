"""请求/响应头脱敏测试。"""

from sona_code.config import RecordingConfig
from sona_code.recording.redact import redact_headers


def _cfg(**over) -> RecordingConfig:
    return RecordingConfig(**over)


def test_default_list_masks_bearer():
    out = redact_headers({"Authorization": "Bearer sk-1234567890"}, _cfg())
    assert out == {"Authorization": "Bearer sk-1***"}


def test_short_value_fully_masked():
    cfg = _cfg()
    # 值整体 ≤6 字符 → ***
    assert redact_headers({"x-api-key": "abc"}, cfg) == {"x-api-key": "***"}
    # scheme 词 + 短凭据
    assert redact_headers({"api-key": "Bearer abcdef"}, cfg) == {"api-key": "Bearer ***"}
    # 长无空格值
    assert redact_headers({"cookie": "session=0123456789"}, cfg) == {"cookie": "sess***"}


def test_custom_redact_list():
    cfg = _cfg(redact_headers=["x-custom"])
    out = redact_headers(
        {"Authorization": "Bearer sk-1234567890", "X-Custom": "secret-value"}, cfg
    )
    # 不在清单内不掩码
    assert out["Authorization"] == "Bearer sk-1234567890"
    assert out["X-Custom"] == "secr***"


def test_redact_disabled_returns_as_is():
    cfg = _cfg(redact=False)
    headers = {"Authorization": "Bearer sk-1234567890", "Cookie": "a=b"}
    assert redact_headers(headers, cfg) == headers


def test_case_insensitive_match_preserves_name_case():
    cfg = _cfg()
    out = redact_headers(
        {"aUtHoRiZaTiOn": "Bearer sk-1234567890", "X-API-KEY": "1234567890", "Ok-Header": "fine"},
        cfg,
    )
    # header 名保留原大小写
    assert set(out) == {"aUtHoRiZaTiOn", "X-API-KEY", "Ok-Header"}
    assert out["aUtHoRiZaTiOn"] == "Bearer sk-1***"
    assert out["X-API-KEY"] == "1234***"
    assert out["Ok-Header"] == "fine"


def test_non_target_headers_untouched():
    cfg = _cfg()
    out = redact_headers({"Content-Type": "application/json", "User-Agent": "ua"}, cfg)
    assert out == {"Content-Type": "application/json", "User-Agent": "ua"}
