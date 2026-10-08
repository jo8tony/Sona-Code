"""Read persisted V1 history without booting a project instance.

OpenCode remains the only writer. These snapshots match the V1 SQLite layout
in the bundled 1.18.32; unsupported/legacy stores fall back to its HTTP API.
Never use immutable SQLite connections: live messages can still be in the WAL.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from sona_code.config import AppConfig


class HistoryNotFound(Exception):
    pass


SESSION_COLUMNS = {
    "id", "project_id", "parent_id", "slug", "directory", "title", "version",
    "time_created", "time_updated", "time_compacting", "time_archived",
    "summary_additions", "summary_deletions", "summary_files", "summary_diffs",
    "share_url", "revert", "permission",
}
HISTORY_ENDPOINT = re.compile(r"^/session/([A-Za-z0-9_-]+)(?:/(message|todo|children))?$")


def _directory(path: str) -> str:
    # V1 SQLite stores portable slash paths; older Windows stores used backslashes.
    return os.path.normcase(os.path.normpath(path.replace("\\", "/"))).replace("\\", "/")


def _database_path(config: AppConfig) -> Path | None:
    env = {**os.environ, **config.terminal.inject_env}
    root = Path(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "opencode"
    override = env.get("OPENCODE_DB")
    if override:
        if override == ":memory:":
            return None
        path = Path(override)
        return path if path.is_absolute() else root / path
    # A custom development channel can use another database. Without its exact
    # channel, defer to the executable rather than display a different dataset.
    if env.get("OPENCODE_DISABLE_CHANNEL_DB") not in {"1", "true"} and any(root.glob("opencode-*.db")):
        return None
    path = root / "opencode.db"
    if not path.exists() and (root / "storage" / "session").exists():
        return None  # Legacy JSON history needs native migration, never fake zero.
    return path


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _json(value: str | None) -> object:
    return json.loads(value) if value is not None else None


def _session(row: sqlite3.Row) -> dict:
    data = dict(row)
    # Refuse a new major storage contract rather than guess its V1 projection.
    if not re.match(r"^1\.", data["version"]):
        raise ValueError("unsupported native history version")
    result = {key: data[key] for key in ("id", "slug", "title", "version")}
    result.update(projectID=data["project_id"], directory=os.path.normpath(data["directory"]))
    result["time"] = {"created": data["time_created"], "updated": data["time_updated"]}
    for key in ("compacting", "archived"):
        if data[f"time_{key}"] is not None:
            result["time"][key] = data[f"time_{key}"]
    for column, key in (("workspace_id", "workspaceID"), ("parent_id", "parentID"),
                        ("path", "path"), ("agent", "agent")):
        if data.get(column) is not None:
            result[key] = data[column]
    for key in ("model", "metadata", "revert", "permission"):
        if data.get(key) is not None:
            result[key] = _json(data[key])
    if data["share_url"]:
        result["share"] = {"url": data["share_url"]}
    if any(data[f"summary_{key}"] is not None for key in ("additions", "deletions", "files")):
        result["summary"] = {key: data[f"summary_{key}"] or 0 for key in ("additions", "deletions", "files")}
        if data["summary_diffs"] is not None:
            result["summary"]["diffs"] = _json(data["summary_diffs"])
    if "cost" in data:
        result["cost"] = data["cost"]
    if all(f"tokens_{key}" in data for key in ("input", "output", "reasoning", "cache_read", "cache_write")):
        result["tokens"] = {key: data[f"tokens_{key}"] for key in ("input", "output", "reasoning")}
        result["tokens"]["cache"] = {key: data[f"tokens_cache_{key}"] for key in ("read", "write")}
    return result


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.1)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    db.execute("BEGIN")  # Session, message and part rows must share one snapshot.
    return db


def session_catalog(projects: list[str], config: AppConfig) -> dict[str, list[dict]] | None:
    """Load counts and summaries for all registered directories in one read."""
    try:
        path = _database_path(config)
        if path is None:
            return None
        result: dict[str, list[dict]] = {project: [] for project in projects}
        if not projects or not path.exists():
            return result
        directories = {_directory(project): project for project in projects}
        with closing(_connect(path)) as db:
            if not SESSION_COLUMNS <= _columns(db, "session"):
                return None
            # Chunk parameters to work with older SQLite's 999-variable limit.
            keys = list(directories)
            for start in range(0, len(keys), 400):
                batch = keys[start:start + 400]
                # Keep Unicode casing in SQL parameters: SQLite NOCASE folds
                # ASCII only, whereas Windows normcase also folds e.g. É.
                paths = [directories[key].replace("\\", "/") for key in batch]
                parameters = [variant for directory in paths for variant in (directory, directory.replace("/", "\\"))]
                collation = " COLLATE NOCASE" if os.name == "nt" else ""
                rows = db.execute(
                    f"SELECT * FROM session WHERE directory{collation} IN ({','.join('?' for _ in parameters)}) "
                    "AND time_archived IS NULL ORDER BY time_updated DESC", parameters,
                )
                for row in rows:
                    result[directories[_directory(row["directory"])]].append(_session(row))
        return result
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return None


def read_history(project: str, config: AppConfig, endpoint: str) -> object | None:
    """Return a supported snapshot, or None to use the authoritative native API."""
    if endpoint == "/session":
        catalog = session_catalog([project], config)
        return catalog[project] if catalog is not None else None
    match = HISTORY_ENDPOINT.fullmatch(endpoint)
    if not match:
        return None
    session_id, resource = match.groups()
    try:
        path = _database_path(config)
        if path is None or not path.is_file():
            return None
        with closing(_connect(path)) as db:
            if not SESSION_COLUMNS <= _columns(db, "session"):
                return None
            row = db.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
            if row is None or _directory(row["directory"]) != _directory(project):
                raise HistoryNotFound(session_id)
            info = _session(row)
            if resource is None:
                return info
            if resource == "children":
                return [_session(child) for child in db.execute(
                    "SELECT * FROM session WHERE parent_id = ?", (session_id,),
                )]
            if resource == "todo":
                if not {"session_id", "content", "status", "priority", "position"} <= _columns(db, "todo"):
                    return None
                return [dict(todo) for todo in db.execute(
                    "SELECT content, status, priority FROM todo WHERE session_id = ? ORDER BY position", (session_id,),
                )]
            if not {"id", "session_id", "time_created", "data"} <= _columns(db, "message"):
                return None
            if not {"id", "session_id", "message_id", "data"} <= _columns(db, "part"):
                return None
            messages = []
            by_id = {}
            for message in db.execute(
                "SELECT * FROM message WHERE session_id = ? ORDER BY time_created, id", (session_id,),
            ):
                item = {"info": {**_json(message["data"]), "id": message["id"], "sessionID": session_id}, "parts": []}
                messages.append(item)
                by_id[message["id"]] = item
            for part in db.execute(
                "SELECT * FROM part WHERE session_id = ? ORDER BY message_id, id", (session_id,),
            ):
                if part["message_id"] in by_id:
                    by_id[part["message_id"]]["parts"].append({
                        **_json(part["data"]), "id": part["id"], "sessionID": session_id, "messageID": part["message_id"],
                    })
            return messages
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return None
