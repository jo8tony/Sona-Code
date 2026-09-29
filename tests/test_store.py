"""CallStore 落盘测试（tmp_path 隔离）。"""

import json

import pytest

from sona_code.admin.api import _load_index_rows
from sona_code.recording.store import CallStore


def make_rec(cid: str, **over) -> dict:
    """构造带合法 call_id（内嵌日期）的最小记录 dict。"""
    rec = {
        "id": cid,
        "status": "ok",
        "started_at": f"{cid[1:5]}-{cid[5:7]}-{cid[7:9]}T10:00:00+08:00",
        "model": "test-model",
        "request": {"method": "POST", "path": "/v1/chat/completions"},
        "response": {"status_code": 200},
        "duration_ms": 5.5,
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        "upstream_name": "main",
    }
    rec.update(over)
    return rec


def test_write_partial_creates_file(tmp_path):
    store = CallStore(tmp_path / "records")
    rec = make_rec("c20240102_101010_aabbcc")
    store.write_partial(rec)
    p = tmp_path / "records" / "calls" / "2024-01-02" / "c20240102_101010_aabbcc.partial.json"
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["id"] == "c20240102_101010_aabbcc"


def test_finalize_replaces_partial_and_appends_index(tmp_path):
    store = CallStore(tmp_path / "records")
    cid = "c20240102_101010_aabbcc"
    rec = make_rec(cid)
    store.write_partial(rec)
    store.finalize(rec)

    d = tmp_path / "records" / "calls" / "2024-01-02"
    assert (d / f"{cid}.json").exists()
    assert not (d / f"{cid}.partial.json").exists()  # partial 已删除
    assert json.loads((d / f"{cid}.json").read_text(encoding="utf-8")) == rec

    rows = store.read_index("2024-01-02")
    assert len(rows) == 1
    assert rows[0] == {
        "id": cid,
        "started_at": rec["started_at"],
        "model": "test-model",
        "path": "/v1/chat/completions",
        "status_code": 200,
        "duration_ms": 5.5,
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
        "upstream_name": "main",
        "status": "ok",
        "session_key": None,
        "preview": "",  # request.parsed 缺失 → 空预览
    }

    # 再定稿一条 → 追加为第二行
    store.finalize(make_rec("c20240102_101011_ddeeff", model="other"))
    rows = store.read_index("2024-01-02")
    assert len(rows) == 2
    assert rows[1]["model"] == "other"


def test_finalize_index_includes_session_key_and_preview(tmp_path):
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec(
        "c20240102_101010_aabbcc",
        session_key="s0123456789abcdef",
        request={
            "method": "POST", "path": "/v1/chat/completions",
            "parsed": {"messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "帮我看看这个问题"},
            ]},
        },
    ))
    row = store.read_index("2024-01-02")[0]
    assert row["session_key"] == "s0123456789abcdef"
    assert row["preview"] == "帮我看看这个问题"


def test_load_call_with_and_without_date(tmp_path):
    store = CallStore(tmp_path / "records")
    rec = make_rec("c20240102_101010_aabbcc")
    store.finalize(rec)
    assert store.load_call("c20240102_101010_aabbcc", "2024-01-02") == rec
    assert store.load_call("c20240102_101010_aabbcc") == rec  # 无 date 时扫描
    assert store.load_call("c20240102_101010_aabbcc", "2024-01-03") is None
    assert store.load_call("c99991231_000000_000000") is None


def test_available_dates_descending(tmp_path):
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec("c20240101_090000_000001"))
    store.finalize(make_rec("c20240103_090000_000003"))
    store.finalize(make_rec("c20240102_090000_000002"))
    assert store.available_dates() == ["2024-01-03", "2024-01-02", "2024-01-01"]


def test_read_index_bad_line_raises(tmp_path):
    # 实际实现：read_index 读异常正常抛出（行级容错在管理 API 侧）
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec("c20240102_101010_aabbcc"))
    idx = tmp_path / "records" / "index" / "2024-01-02.jsonl"
    idx.write_text("{broken\n" + idx.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        store.read_index("2024-01-02")


def test_admin_load_index_rows_skips_bad_lines(tmp_path):
    # 管理端 _load_index_rows：坏行跳过、空行跳过、好行保留
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec("c20240102_101010_aabbcc"))
    idx = tmp_path / "records" / "index" / "2024-01-02.jsonl"
    idx.write_text(idx.read_text(encoding="utf-8") + "{broken\n\n[1,2]\n", encoding="utf-8")
    pairs = _load_index_rows(store, ["2024-01-02"])
    assert [r["id"] for _, r in pairs] == ["c20240102_101010_aabbcc"]


# ------------------------------------------------------------------ 清理功能
def test_delete_call_removes_files_and_index_line(tmp_path):
    store = CallStore(tmp_path / "records")
    a = "c20240102_101010_aabbcc"
    b = "c20240102_101011_ddeeff"
    store.finalize(make_rec(a))
    store.finalize(make_rec(b))

    assert store.delete_call(a) is True
    assert store.load_call(a) is None
    assert [r["id"] for r in store.read_index("2024-01-02")] == [b]  # 索引行已移除
    assert store.delete_call("c99991231_000000_000000") is False  # 不存在 → False


def test_delete_call_keeps_bad_index_lines(tmp_path):
    # 索引中坏行在删除时原样保留
    store = CallStore(tmp_path / "records")
    a = "c20240102_101010_aabbcc"
    store.finalize(make_rec(a))
    idx = tmp_path / "records" / "index" / "2024-01-02.jsonl"
    idx.write_text("{broken\n" + idx.read_text(encoding="utf-8"), encoding="utf-8")
    store.delete_call(a)
    assert "{broken" in idx.read_text(encoding="utf-8")


def test_delete_call_removes_empty_date_dir(tmp_path):
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec("c20240102_101010_aabbcc"))
    store.delete_call("c20240102_101010_aabbcc")
    assert not (tmp_path / "records" / "calls" / "2024-01-02").exists()
    assert store.available_dates() == []


def test_delete_date_and_delete_all(tmp_path):
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec("c20240101_090000_000001"))
    store.finalize(make_rec("c20240102_090000_000002"))
    store.finalize(make_rec("c20240103_090000_000003"))

    n = store.delete_date("2024-01-02")
    assert n == 1
    assert store.available_dates() == ["2024-01-03", "2024-01-01"]
    assert not (tmp_path / "records" / "index" / "2024-01-02.jsonl").exists()

    r = store.delete_all()
    assert r == {"dates": 2, "calls": 2}
    assert store.available_dates() == []


def test_delete_date_rejects_bad_name(tmp_path):
    store = CallStore(tmp_path / "records")
    with pytest.raises(ValueError):
        store.delete_date("../etc")


def test_cleanup_older_than(tmp_path):
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec("c20200101_090000_000001"))  # 很老
    store.finalize(make_rec("c29991231_090000_000003"))  # 很新（未来）
    today_recent = "c" + __import__("datetime").datetime.now().strftime("%Y%m%d") + "_090000_x"
    store.finalize(make_rec(today_recent))

    # 保留 30 天：只删 2020 的
    removed = store.cleanup_older_than(30)
    assert removed == ["2020-01-01"]
    assert store.available_dates() == ["2999-12-31", today_recent[1:9][:4] + "-" + today_recent[1:9][4:6] + "-" + today_recent[1:9][6:8]]

    # retention=0 / 负数：不动
    assert store.cleanup_older_than(0) == []


def test_stats_reports_per_date(tmp_path):
    store = CallStore(tmp_path / "records")
    store.finalize(make_rec("c20240102_101010_aabbcc"))
    store.write_partial(make_rec("c20240102_101011_ddeeff"))  # partial 计文件不计记录
    s = store.stats()
    assert s["total_files"] == 2  # 1 final + 1 partial（index 只计 bytes）
    assert s["total_bytes"] > 0
    assert len(s["dates"]) == 1
    d = s["dates"][0]
    assert d["date"] == "2024-01-02"
    assert d["calls"] == 1  # 仅 final 计记录数
    assert d["files"] == 2
