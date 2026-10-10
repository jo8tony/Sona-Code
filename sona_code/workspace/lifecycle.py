"""Application execution metadata, without modifying native history storage."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path


class ExecutionLedger:
    def __init__(self, config_path: str) -> None:
        self.path = Path(config_path).resolve().parent / "workspace-executions.json"
        self.runs = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        changed = False
        for runs in self.runs.values():
            for run in runs:
                if run["state"] in {"running", "stopping"}:
                    run.update(state="interrupted", end=int(time.time() * 1000))
                    changed = True
        if changed:
            self.save()

    @staticmethod
    def key(project: str, session: str) -> str:
        return json.dumps([os.path.normcase(str(Path(project).resolve())), session])

    def save(self) -> None:
        if not self.runs:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".executions-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.runs, stream, ensure_ascii=False)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def begin(self, project: str, session: str) -> None:
        runs = self.runs.setdefault(self.key(project, session), [])
        if runs and runs[-1]["state"] in {"running", "stopping"}:
            return
        runs.append({"start": int(time.time() * 1000), "state": "running"})
        self.save()

    def finish(self, project: str, session: str, state: str) -> None:
        runs = self.runs.get(self.key(project, session), [])
        if runs and runs[-1]["state"] in {"running", "stopping"}:
            runs[-1].update(state=state, end=int(time.time() * 1000))
            self.save()

    def finish_project(self, project: str) -> None:
        project = os.path.normcase(str(Path(project).resolve()))
        for key in list(self.runs):
            path, session = json.loads(key)
            if path == project:
                self.finish(project, session, "interrupted")

    def cancel(self, project: str, session: str, ended: int | None = None) -> None:
        ended = ended if isinstance(ended, int) else int(time.time() * 1000)
        for run in reversed(self.runs.get(self.key(project, session), [])):
            if run["start"] <= ended:
                run.update(state="stopped", end=max(run.get("end", ended), ended))
                self.save()
                return

    def annotate(self, project: str, session: str, messages: list) -> list:
        messages = copy.deepcopy(messages)
        runs = self.runs.get(self.key(project, session), [])
        for message in messages:
            info = message.get("info", {})
            created = info.get("time", {}).get("created", 0)
            stopped = next((run for run in reversed(runs) if run["state"] in {"stopped", "interrupted"}
                            and run["start"] <= created <= run["end"]), None)
            if not stopped or info.get("role") != "assistant":
                continue
            end = stopped["end"]
            if not info.get("time", {}).get("completed"):
                info.setdefault("time", {})["completed"] = end
                info["error"] = {"name": "MessageAbortedError", "data": {"message": "任务已中断"}}
            for part in message.get("parts", []):
                state = part.get("state", {})
                aborted_shell = str(state.get("output", "")).endswith("<metadata>\nUser aborted the command\n</metadata>")
                cancelled = state.get("status") == "error" and state.get("error") in {"Cancelled", "Interrupted", "The operation was aborted."}
                if part.get("type") == "tool" and (state.get("status") in {"running", "pending"} or aborted_shell or cancelled):
                    state.update(status="error", error="任务已停止", metadata={**state.get("metadata", {}), "interrupted": True})
                    state.setdefault("time", {})["end"] = end
                if part.get("type") in {"reasoning", "text"} and "time" in part:
                    part["time"].setdefault("end", end)
        return messages
