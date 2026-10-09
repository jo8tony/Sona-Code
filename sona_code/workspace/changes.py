"""Project V1 file changes from history without reading or modifying project files.

Native turn summaries take priority. Complete read displays can recover a write
comparison when V1's write result omits its diff. Incomplete history stays unknown.
"""

from __future__ import annotations

import copy
import difflib
import re

MAX_DIFF_LINES = 10_000
MAX_DIFF_CHARS = 2_000_000
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
EDIT_TOOLS = {"write", "edit", "apply_patch", "multiedit"}
READ_ONLY_TOOLS = {"read", "glob", "grep", "list", "todowrite", "todoread", "webfetch", "question", "skill"}


def _file(value: str, project: str) -> str:
    value = value.replace("\\", "/")
    root = project.replace("\\", "/").rstrip("/")
    # Windows native paths can differ in drive-letter casing.
    prefix, path = (root.casefold(), value.casefold()) if ":" in root else (root, value)
    return value[len(root) + 1:] if path.startswith(prefix + "/") else value


def _lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def _text_diff(file: str, before: str, after: str) -> dict | None:
    if len(before) + len(after) > MAX_DIFF_CHARS:
        return None
    old, new = _lines(before), _lines(after)
    if len(old) + len(new) > MAX_DIFF_LINES:
        return None
    return _line_diff(file, old, new)


def _line_diff(file: str, old: list[str], new: list[str], context: int = 3) -> dict:
    additions = deletions = 0
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag != "equal":
            deletions += b - a
            additions += d - c
    rows = []
    for group in matcher.get_grouped_opcodes(context):
        if not rows:
            rows.extend([f"--- {file}", f"+++ {file}"])
        a, b, c, d = group[0][1], group[-1][2], group[0][3], group[-1][4]
        rows.append(f"@@ -{a + 1 if b > a else a},{b - a} +{c + 1 if d > c else c},{d - c} @@")
        for tag, a, b, c, d in group:
            if tag == "equal":
                rows.extend(" " + line for line in old[a:b])
            else:
                rows.extend("-" + line for line in old[a:b])
                rows.extend("+" + line for line in new[c:d])
    return {"file": file, "patch": "\n".join(rows), "additions": additions, "deletions": deletions, "net": True}


def _compose(file: str, edits: list[dict]) -> dict | None:
    """Apply successive unified patches to a shared, partly known baseline.

    Unknown unchanged lines retain identities in both versions. Zero-context
    output contains only real changed lines, never fabricated file content.
    A conflicting or unavailable patch leaves the totals explicitly cumulative.
    """
    original: list[list[str | None]] = []
    current: list[list[str | None]] = []
    for edit in edits:
        patch = edit.get("patch")
        if (not isinstance(patch, str) or not patch or "\x00" in patch or "\\ No newline" in patch or
                len(patch) > MAX_DIFF_CHARS or
                edit.get("countsPartial") or edit.get("countsUnknown")):
            return None
        rows = patch.split("\n")
        cursor = 0
        offset = 0
        found = False
        while cursor < len(rows):
            match = HUNK.match(rows[cursor])
            cursor += 1
            if not match:
                continue
            found = True
            start, count, new_start, new_count = (int(v) if v is not None else 1 for v in match.groups())
            index = (start if count == 0 else start - 1) + offset
            expected = new_start if new_count == 0 else new_start - 1
            if index < 0 or index != expected or index + count > MAX_DIFF_LINES:
                return None
            while len(current) < index + count:
                line = [None]
                original.append(line)
                current.append(line)
            replacement = []
            consumed = 0
            while cursor < len(rows) and not HUNK.match(rows[cursor]):
                row = rows[cursor]
                cursor += 1
                if row.startswith("\\ No newline"):
                    continue
                if consumed == count and len(replacement) == new_count:
                    break
                if not row or row[0] not in " +-":
                    return None
                if row[0] in " -":
                    if consumed >= count:
                        return None
                    line = current[index + consumed]
                    if line[0] is not None and line[0] != row[1:]:
                        return None
                    line[0] = row[1:]
                    consumed += 1
                    if row[0] == " ":
                        replacement.append(line)
                else:
                    replacement.append([row[1:]])
            if consumed != count or len(replacement) != new_count:
                return None
            current[index:index + count] = replacement
            offset += new_count - count
            if len(original) + len(current) > MAX_DIFF_LINES:
                return None
        if not found:
            return None
    def token(line: list[str | None]) -> str:
        return line[0] if line[0] is not None else f"\x00unchanged:{id(line)}"
    return _line_diff(file, list(map(token, original)), list(map(token, current)), context=0)


def combine_changes(changes: list[dict]) -> list[dict]:
    """Return net changes where patches agree, otherwise labelled activity totals."""
    files: dict[str, list[dict]] = {}
    for change in changes:
        if change.get("file"):
            files.setdefault(change["file"], []).extend(change.get("toolEdits") or [change])
    result = []
    for file, edits in files.items():
        if len(edits) == 1:
            combined = edits[0]
        else:
            combined = _compose(file, edits)
            if combined is not None:
                initial_exists = edits[0].get("status") != "added"
                final_exists = edits[-1].get("status") != "deleted"
                if not initial_exists and not final_exists and combined["additions"] == combined["deletions"] == 0:
                    continue
                combined["status"] = "modified" if initial_exists and final_exists else "added" if final_exists else "deleted"
                if any(e.get("comparison") == "read-write" for e in edits):
                    combined["comparison"] = "read-write"
            if combined is None:
                known = [e for e in edits if not e.get("countsUnknown") and
                         isinstance(e.get("additions"), int) and isinstance(e.get("deletions"), int)]
                combined = {"file": file, "toolEdits": edits, "cumulative": True,
                            "patch": "\n".join(e.get("patch") or "" for e in edits),
                            "countsUnknown": not known,
                            "countsPartial": len(known) != len(edits) or any(e.get("countsPartial") for e in edits),
                            "additions": sum(e["additions"] for e in known),
                            "deletions": sum(e["deletions"] for e in known),
                            "writtenLines": sum(e.get("writtenLines", 0) for e in edits)}
                if not combined["patch"]:
                    combined.update(derived=True, input=edits[-1].get("input", {}))
        # A successful write that restores the baseline is not a changed file.
        if (combined.get("net") and not combined.get("countsUnknown") and
                combined.get("additions") == combined.get("deletions") == 0 and
                combined.get("status", "modified") == "modified" and not combined.get("binary")):
            continue
        result.append(combined)
    return result


def _turn_changes(replies: list[dict], project: str) -> list[dict]:
    changes = []
    reads: dict[str, tuple[str, int | None]] = {}
    for reply in replies:
        for part in reply.get("parts", []):
            if part.get("type") != "tool":
                continue
            tool, state = part.get("tool"), part.get("state", {})
            if tool not in EDIT_TOOLS | READ_ONLY_TOOLS:
                reads.clear()  # Shell commands, subtasks and custom tools can change files.
            if state.get("status") != "completed":
                if tool in EDIT_TOOLS:
                    reads.clear()
                continue
            input, metadata = state.get("input", {}), state.get("metadata", {})
            file = _file(input.get("filePath") or input.get("path") or metadata.get("filepath") or "", project)
            if tool == "read":
                display = metadata.get("display", {})
                text = display.get("text")
                if (display.get("type") == "file" and display.get("lineStart") == 1 and
                        display.get("lineEnd") == display.get("totalLines") and
                        display.get("truncated") is False and metadata.get("truncated") is not True and
                        isinstance(text, str) and "... (line truncated to 2000 chars)" not in text):
                    total = display.get("totalLines")
                    if isinstance(total, int) and (len(text.split("\n")) if total else 0) == total:
                        # display.text joins the returned lines and omits the
                        # EOF newline. Preserve a real final blank content line.
                        reads[_file(display.get("path") or file, project)] = (
                            text + "\n" if total else "", state.get("time", {}).get("end"))
                continue
            if tool not in EDIT_TOOLS:
                continue
            native = metadata.get("files")
            if isinstance(native, list):
                edits = [{**item, "file": item.get("relativePath") or item.get("filePath") or item.get("file"),
                          "patch": item.get("diff") or item.get("patch", "")} for item in native]
            elif isinstance(metadata.get("filediff"), dict):
                edits = [dict(metadata["filediff"])]
            else:
                edits = [{"file": file, "patch": metadata.get("diff") or "", "derived": not metadata.get("diff"),
                          "input": input, "countsUnknown": not metadata.get("diff")}]
                if tool == "write" and isinstance(input.get("content"), str):
                    edits[0]["writtenLines"] = len(_lines(input["content"]))
                    baseline = reads.get(file)
                    start = state.get("time", {}).get("start")
                    if baseline and start is not None and baseline[1] is not None and baseline[1] > start:
                        baseline = None  # A parallel read is not a pre-write baseline.
                    before = "" if metadata.get("exists") is False else baseline[0] if baseline else None
                    if before is not None:
                        diff = _text_diff(file, before, input["content"])
                        if diff is not None:
                            edits = [{**diff, "status": "added" if metadata.get("exists") is False else "modified",
                                      "comparison": "read-write" if metadata.get("exists") is not False else "created"}]
                elif isinstance(input.get("oldString"), str) and isinstance(input.get("newString"), str):
                    # These are replacement fragments, not necessarily changed lines.
                    diff = _text_diff(file, input["oldString"], input["newString"])
                    if diff is not None:
                        edits[0].update(additions=diff["additions"], deletions=diff["deletions"],
                                        countsUnknown=False, cumulative=True, countsPartial=input.get("replaceAll") is True)
            for edit in edits:
                if not isinstance(edit.get("file"), str) or not edit["file"]:
                    continue
                edit["file"] = _file(edit["file"], project)
                if "status" not in edit and edit.get("type") in {"add", "delete", "update"}:
                    edit["status"] = {"add": "added", "delete": "deleted", "update": "modified"}[edit["type"]]
                if edit.get("patch") and "additions" not in edit:
                    rows = edit["patch"].split("\n")
                    edit.update(additions=sum(row.startswith("+") and not row.startswith("+++ ") for row in rows),
                                deletions=sum(row.startswith("-") and not row.startswith("--- ") for row in rows))
                changes.append(edit)
                reads.pop(edit["file"], None)
    return combine_changes(changes)


def annotate_changes(messages: list[dict], project: str) -> list[dict]:
    """Attach application comparisons to user messages; native history stays untouched."""
    messages = copy.deepcopy(messages)
    replies: dict[str, list[dict]] = {}
    for message in messages:
        info = message.get("info", {})
        if info.get("role") == "assistant":
            replies.setdefault(info.get("parentID", ""), []).append(message)
    for message in messages:
        info = message.get("info", {})
        if info.get("role") == "user":
            info["sonaDiffs"] = _turn_changes(replies.get(info.get("id", ""), []), project)
    return messages


def history_changes(messages: list[dict], project: str, message_id: str | None = None) -> list[dict]:
    changes = []
    for message in annotate_changes(messages, project):
        info = message.get("info", {})
        if info.get("role") != "user" or message_id is not None and info.get("id") != message_id:
            continue
        native = info.get("summary", {}).get("diffs")
        diffs = native if isinstance(native, list) and native else info.get("sonaDiffs", [])
        for diff in diffs:
            file = diff.get("file") or diff.get("path")
            if isinstance(file, str) and file:
                changes.append({**diff, "file": _file(file, project)})
    return combine_changes(changes)
