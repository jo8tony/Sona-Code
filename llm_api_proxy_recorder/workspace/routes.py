"""Project and conversation API backed by headless OpenCode."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import secrets
import time
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from llm_api_proxy_recorder.admin.skills import WORKSPACE_COMMANDS
from llm_api_proxy_recorder.terminal.manager import resolve_opencode
from llm_api_proxy_recorder.admin.models import application_catalog, public_native_catalog, native_provider_id
from llm_api_proxy_recorder.workspace.manager import WorkspaceError
from llm_api_proxy_recorder.recording.parse import session_key_from_header

router = APIRouter()


def _project_id(path: str) -> str:
    normalized = os.path.normcase(str(Path(path).expanduser().resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _project_info(item: dict) -> dict:
    path = str(Path(item["path"]).expanduser().resolve())
    return {"id": _project_id(path), "path": path, "name": item.get("name") or Path(path).name or path}


def _project_path(request: Request, project_id: str) -> str:
    for item in request.app.state.runtime.terminal_projects.list():
        if _project_id(item["path"]) == project_id:
            return str(Path(item["path"]).expanduser().resolve())
    raise HTTPException(status_code=404, detail="项目不存在")


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise HTTPException(status_code=400, detail="无效的会话或权限 ID")
    return quote(value, safe="")


async def _opencode(
    request: Request, project: str, method: str, endpoint: str,
    body: dict | None = None, params: dict[str, str | int] | None = None,
):
    try:
        runtime = request.app.state.runtime
        if method == "POST" and endpoint.endswith(("/prompt_async", "/command", "/shell", "/summarize")):
            async with runtime.workspace.task_dispatch():
                body = dict(body or {})
                selected = body.get("model")
                provider_id, model_id = None, None
                if isinstance(selected, dict):
                    provider_id, model_id = selected.get("providerID"), selected.get("modelID")
                elif isinstance(selected, str):
                    provider_id, _, model_id = selected.partition("/")
                elif endpoint.endswith("/summarize"):
                    provider_id, model_id = body.get("providerID"), body.get("modelID")
                choice = _model_choice(provider_id, model_id, request, body.get("variant"))
                if choice:
                    if endpoint.endswith("/summarize"):
                        body.update(choice)
                    else:
                        body["model"] = f"{choice['providerID']}/{choice['modelID']}" if endpoint.endswith("/command") else choice
                    provider = next((p for p in runtime.config.upstreams if native_provider_id(p.name) == choice["providerID"]), None)
                    model = next((m for m in provider.models if m.id == choice["modelID"]), None) if provider else None
                    if model:
                        for part in body.get("parts", []):
                            mime = part.get("mime", "")
                            required = "image" if mime.startswith("image/") else "pdf" if mime == "application/pdf" else None
                            if required and required not in model.input_modalities:
                                raise HTTPException(400, "所选模型不支持该附件类型")
                return await runtime.workspace.request(
                    project, runtime.config, method, endpoint, body=body, params=params)
        return await runtime.workspace.request(
            project, runtime.config, method, endpoint, body=body, params=params)

    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/workspace/check")
def check(request: Request) -> dict:
    config = request.app.state.runtime.config
    resolution = resolve_opencode(config)
    terminal = config.terminal
    return {
        "found": bool(resolution.path), "source": resolution.source,
        "provider_hint": terminal.opencode_provider or terminal.proxy_upstream or config.default_upstream,
    }


@router.get("/workspace/projects")
def list_projects(request: Request) -> dict:
    items = [_project_info(item) for item in request.app.state.runtime.terminal_projects.list()]
    return {"items": items, "total": len(items)}


class AddProjectBody(BaseModel):
    path: str = Field(min_length=1)


@router.post("/workspace/projects", status_code=201)
def add_project(body: AddProjectBody, request: Request) -> dict:
    path = Path(body.path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="项目目录不存在")
    projects = request.app.state.runtime.terminal_projects
    existing = next((item for item in projects.list() if item["path"] == str(path)), None)
    kind = existing.get("kind", "opencode") if existing else "opencode"
    item = projects.add(str(path), kind)
    return _project_info(item)


@router.delete("/workspace/projects/{project_id}")
async def remove_project(project_id: str, request: Request) -> dict:
    path = _project_path(request, project_id)
    deleted = request.app.state.runtime.terminal_projects.delete(path)
    await request.app.state.runtime.workspace.stop(path)
    return {"ok": deleted}


class RenameProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/workspace/projects/{project_id}")
def rename_project(project_id: str, body: RenameProjectBody, request: Request) -> dict:
    path = _project_path(request, project_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请输入工作区名称")
    item = request.app.state.runtime.terminal_projects.rename(path, name)
    if item is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return _project_info(item)


@router.post("/workspace/projects/{project_id}/open")
def open_project_directory(project_id: str, request: Request) -> dict:
    path = _project_path(request, project_id)
    if not Path(path).is_dir():
        raise HTTPException(status_code=404, detail="项目目录不存在")
    command = ["open", path] if sys.platform == "darwin" else (
        ["explorer", path] if os.name == "nt" else ["xdg-open", path]
    )
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法打开目录：{exc.strerror or exc}") from exc
    return {"ok": True}


@router.get("/workspace/projects/{project_id}/sessions")
async def list_sessions(project_id: str, request: Request):
    path = _project_path(request, project_id)
    data = await _opencode(request, path, "GET", "/session")
    return {"items": data if isinstance(data, list) else []}


class CreateSessionBody(BaseModel):
    title: str | None = Field(default=None, max_length=200)


@router.post("/workspace/projects/{project_id}/sessions", status_code=201)
async def create_session(project_id: str, body: CreateSessionBody, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", "/session", {"title": body.title} if body.title else {})


@router.get("/workspace/projects/{project_id}/sessions/{session_id}")
async def get_session(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", f"/session/{_safe_id(session_id)}")


class RenameSessionBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.patch("/workspace/projects/{project_id}/sessions/{session_id}")
async def rename_session(project_id: str, session_id: str, body: RenameSessionBody, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "PATCH", f"/session/{_safe_id(session_id)}", {"title": body.title})


@router.delete("/workspace/projects/{project_id}/sessions/{session_id}")
async def delete_session(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "DELETE", f"/session/{_safe_id(session_id)}")


class ForkSessionBody(BaseModel):
    message_id: str | None = None


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/fork")
async def fork_session(project_id: str, session_id: str, body: ForkSessionBody, request: Request):
    path = _project_path(request, project_id)
    session = _safe_id(session_id)
    payload = {}
    if body.message_id:
        message_id = _safe_id(body.message_id)
        messages = await _opencode(request, path, "GET", f"/session/{session}/message")
        index = next((i for i, message in enumerate(messages)
                      if message.get("info", {}).get("id") == message_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="分支消息不存在")
        # OpenCode excludes the boundary message. Use the next message to retain
        # the selected reply; omitting the boundary retains the final reply.
        if index + 1 < len(messages):
            payload["messageID"] = _safe_id(messages[index + 1]["info"]["id"])
    return await _opencode(request, path, "POST", f"/session/{session}/fork", payload)


@router.get("/workspace/projects/{project_id}/sessions/{session_id}/children")
async def session_children(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", f"/session/{_safe_id(session_id)}/children")


@router.get("/workspace/projects/{project_id}/sessions/{session_id}/todo")
async def session_todo(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", f"/session/{_safe_id(session_id)}/todo")


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/share")
async def share_session(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/share", {})


@router.delete("/workspace/projects/{project_id}/sessions/{session_id}/share")
async def unshare_session(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "DELETE", f"/session/{_safe_id(session_id)}/share")


class RevertMessageBody(BaseModel):
    message_id: str = Field(min_length=1)
    part_id: str | None = None


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/withdraw")
async def withdraw_turn(project_id: str, session_id: str, body: RevertMessageBody, request: Request):
    """Delete the latest user turn through V1, preserving project file changes."""
    path = _project_path(request, project_id)
    session = _safe_id(session_id)
    message_id = _safe_id(body.message_id)
    statuses = await _opencode(request, path, "GET", "/session/status")
    if statuses.get(session_id, {}).get("type", "idle") != "idle":
        raise HTTPException(409, "请先停止任务或等待任务结束，再撤回消息")
    messages = await _opencode(request, path, "GET", f"/session/{session}/message")
    index = next((i for i in range(len(messages) - 1, -1, -1)
                  if messages[i].get("info", {}).get("role") == "user"
                  and not any(part.get("type") == "compaction"
                              for part in messages[i].get("parts", []))), None)
    if index is None or messages[index]["info"]["id"] != message_id:
        raise HTTPException(409, "只能撤回最后一轮用户消息，请刷新对话后重试")
    # Keep the user boundary until every reply is removed so a failed deletion
    # can be retried. Native delete rejects busy sessions and removes all parts.
    for message in reversed(messages[index:]):
        await _opencode(request, path, "DELETE",
                        f"/session/{session}/message/{_safe_id(message['info']['id'])}")
    return {"ok": True}


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/revert")
async def revert_message(project_id: str, session_id: str, body: RevertMessageBody, request: Request):
    path = _project_path(request, project_id)
    payload = {"messageID": _safe_id(body.message_id)}
    if body.part_id:
        payload["partID"] = _safe_id(body.part_id)
    return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/revert", payload)


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/unrevert")
async def unrevert_session(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/unrevert", {})


class SummarizeSessionBody(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/summarize")
async def summarize_session(project_id: str, session_id: str, body: SummarizeSessionBody, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/summarize",
                           _model_choice(body.provider_id, body.model_id, request))


@router.get("/workspace/projects/{project_id}/status")
async def project_status(project_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", "/session/status")


@router.get("/workspace/projects/{project_id}/models")
async def project_models(project_id: str, request: Request):
    path = _project_path(request, project_id)
    cfg = request.app.state.runtime.config
    providers = application_catalog(cfg)
    connected = []
    if cfg.model_settings.show_native_models:
        data = await _opencode(request, path, "GET", "/config/providers")
        auth = await _opencode(request, path, "GET", "/provider")
        providers.extend(public_native_catalog(data))
        connected = auth.get("connected", []) if isinstance(auth, dict) else []
    choice = cfg.model_settings.default_model
    default = {"providerID": native_provider_id(choice.provider), "modelID": choice.model} if choice else None
    if not default and providers and providers[0]["models"]:
        default = {"providerID": providers[0]["id"], "modelID": next(iter(providers[0]["models"]))}
    return {"providers": providers, "connected": connected, "default_model": default,
            "show_native_models": cfg.model_settings.show_native_models}



class ProviderApiKeyBody(BaseModel):
    key: str = Field(min_length=1, max_length=10_000)


@router.post("/workspace/projects/{project_id}/providers/{provider_id}/api-key")
async def save_provider_api_key(project_id: str, provider_id: str, body: ProviderApiKeyBody, request: Request):
    if provider_id.startswith("llmpr-"):
        raise HTTPException(409, "应用提供商密钥请在模型页面管理")
    path = _project_path(request, project_id)
    await _opencode(request, path, "PUT", f"/auth/{_safe_id(provider_id)}", {"type": "api", "key": body.key})
    return {"ok": True, "provider_id": provider_id, "configured": True}


@router.get("/workspace/projects/{project_id}/agents")
async def project_agents(project_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", "/agent")


@router.get("/workspace/projects/{project_id}/commands")
async def project_commands(project_id: str, request: Request):
    path = _project_path(request, project_id)
    commands = await _opencode(request, path, "GET", "/command")
    store = request.app.state.runtime.skills
    installed = await asyncio.to_thread(store.list)
    disabled = {item["name"] for item in installed["items"] if not item["enabled"]}
    return [item for item in commands if item.get("source") != "skill" or item.get("name") not in disabled]


@router.get("/workspace/projects/{project_id}/files")
async def search_project_files(
    project_id: str, request: Request, query: str = Query(default="", max_length=200),
):
    root = Path(_project_path(request, project_id)).resolve()
    matches = await _opencode(
        request, str(root), "GET", "/find/file",
        params={"query": query, "type": "file", "limit": 30},
    )
    if not isinstance(matches, list):
        return {"items": []}

    items = []
    for match in matches:
        if not isinstance(match, str) or not match or "\x00" in match:
            continue
        normalized = match.replace("\\", "/")
        segments = normalized.split("/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or any(
            part in {"", ".", ".."} for part in segments
        ):
            continue
        try:
            candidate = root.joinpath(*segments).resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, ValueError):
            continue
        if candidate.is_file():
            items.append("/".join(segments))
    return {"items": list(dict.fromkeys(items))}


@router.get("/workspace/projects/{project_id}/sessions/{session_id}/messages")
async def list_messages(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    messages = await _opencode(request, path, "GET", f"/session/{_safe_id(session_id)}/message")
    return await asyncio.to_thread(request.app.state.runtime.skills.annotate_messages, path, session_id, messages)


@router.get("/workspace/projects/{project_id}/sessions/{session_id}/trajectory")
def session_trajectory(project_id: str, session_id: str, request: Request) -> dict:
    """Reuse the recorder ledger for OpenCode's explicit X-Session-Id."""
    from llm_api_proxy_recorder.admin.api import trajectory_session_detail

    _project_path(request, project_id)
    key = session_key_from_header(_safe_id(session_id))
    return trajectory_session_detail(key, request, q=None)


class PromptBody(BaseModel):
    text: str = Field(default="", max_length=100_000)
    files: list["PromptFile"] = Field(default_factory=list, max_length=8)
    references: list["PromptReference"] = Field(default_factory=list, max_length=8)
    provider_id: str | None = None
    model_id: str | None = None
    agent: str | None = None
    variant: str | None = Field(default=None, max_length=100)


class PromptFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=14_000_000)


class PromptReference(BaseModel):
    path: str = Field(min_length=1, max_length=1_000)


def _prompt_file_part(file: PromptFile) -> tuple[dict, int]:
    # Data URLs come from the browser file picker. Accept only the media types
    # OpenCode can consume, and avoid passing arbitrary URLs to its server.
    if file.mime not in {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"}:
        raise HTTPException(status_code=400, detail="不支持的附件类型")
    if "/" in file.filename or "\\" in file.filename or file.filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="无效的附件名称")
    prefix = f"data:{file.mime};base64,"
    if not file.url.startswith(prefix):
        raise HTTPException(status_code=400, detail="附件必须是匹配 MIME 类型的 base64 数据")
    try:
        decoded = base64.b64decode(file.url[len(prefix):], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="附件内容无效") from exc
    if len(decoded) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单个附件不能超过 8 MB")
    return {"type": "file", "filename": file.filename, "mime": file.mime, "url": file.url}, len(decoded)


def _model_choice(provider_id: str | None, model_id: str | None, request: Request,
                  variant: str | None = None) -> dict | None:
    cfg = request.app.state.runtime.config
    if bool(provider_id) != bool(model_id):
        raise HTTPException(400, "请选择完整的提供商与模型")
    if not provider_id:
        choice = cfg.model_settings.default_model
        if choice:
            provider_id, model_id = native_provider_id(choice.provider), choice.model
        else:
            first = next((p for p in cfg.upstreams if p.models), None)
            if first:
                provider_id, model_id = native_provider_id(first.name), first.models[0].id
            elif cfg.model_settings.show_native_models:
                return None
            else:
                raise HTTPException(409, "暂无可用模型，请前往模型页面添加模型")
    provider = next((p for p in cfg.upstreams if native_provider_id(p.name) == provider_id), None)
    if provider:
        model = next((m for m in provider.models if m.id == model_id), None)
        if not model:
            raise HTTPException(409, "所选模型已删除，请重新选择")
        if variant and variant not in model.reasoning_efforts:
            raise HTTPException(400, "该模型不支持所选思考强度")
    elif provider_id.startswith("llmpr-") or not cfg.model_settings.show_native_models:
        raise HTTPException(409, "所选提供商不可用，请重新选择模型")
    return {"providerID": provider_id, "modelID": model_id}


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/prompt")
async def send_prompt(project_id: str, session_id: str, body: PromptBody, request: Request):
    path = _project_path(request, project_id)
    if len(body.files) + len(body.references) > 8:
        raise HTTPException(status_code=400, detail="一条消息最多添加 8 个附件或文件引用")
    if not body.text.strip() and not body.files and not body.references:
        raise HTTPException(status_code=400, detail="请输入消息或添加附件")
    parts = [{"type": "text", "text": body.text}] if body.text.strip() else []
    file_parts = [_prompt_file_part(file) for file in body.files]
    if sum(size for _, size in file_parts) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="附件总大小不能超过 20 MB")
    parts.extend(part for part, _ in file_parts)
    project_root = Path(path).resolve()
    references = set()
    for reference in body.references:
        if "\\" in reference.path or "\x00" in reference.path or re.match(r"^[A-Za-z]:", reference.path):
            raise HTTPException(status_code=400, detail="无效的项目文件路径")
        relative = Path(reference.path)
        if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
            raise HTTPException(status_code=400, detail="无效的项目文件路径")
        try:
            candidate = (project_root / relative).resolve(strict=True)
            candidate.relative_to(project_root)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="文件不存在或不在当前项目内") from exc
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail="引用路径不是文件")
        relative_name = candidate.relative_to(project_root).as_posix()
        if relative_name in references:
            continue
        references.add(relative_name)
        parts.append({
            "type": "file", "filename": relative_name, "mime": "text/plain",
            "url": candidate.as_uri(),
        })
    prompt: dict = {"parts": parts}
    model = _model_choice(body.provider_id, body.model_id, request, getattr(body, "variant", None))
    if model:
        prompt["model"] = model
    if body.agent:
        prompt["agent"] = body.agent
    if body.variant:
        prompt["variant"] = body.variant
    return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/prompt_async", prompt)


class CommandBody(BaseModel):
    command: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    arguments: str = Field(default="", max_length=100_000)
    provider_id: str | None = None
    model_id: str | None = None
    agent: str | None = None
    variant: str | None = Field(default=None, max_length=100)


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/command")
async def run_command(project_id: str, session_id: str, body: CommandBody, request: Request):
    async with request.app.state.runtime.workspace.task_dispatch():
        path = _project_path(request, project_id)
        installed = await asyncio.to_thread(request.app.state.runtime.skills.list)
        managed = next((item for item in installed["items"]
                        if item["name"] == body.command and not item.get("conflict")), None)
        catalog = None
        if managed is None:
            catalog = await _native_skill_catalog(request, path)
            managed = next((item for item in _native_skill_items(catalog) if item["name"] == body.command), None)
            if managed:
                managed["enabled"] = await asyncio.to_thread(request.app.state.runtime.skills.permission, body.command) != "deny"
        if managed:
            if not managed["enabled"] or managed.get("error"):
                raise HTTPException(status_code=409, detail="该技能已停用或格式无效，请重新选择技能")
            available = await _native_managed_skill_names(request, path, [managed], catalog)
            if body.command not in available:
                raise HTTPException(status_code=409, detail="OpenCode 未加载该技能或存在同名技能/命令，请检查配置")
        if managed:
            if not await _agent_allows_skill(request, path, body.command, body.agent):
                raise HTTPException(409, "当前 Agent 禁止使用该技能，请切换 Agent 或修改权限")
        payload: dict = {"command": body.command, "arguments": body.arguments}
        if managed:
            message_id = "msg_" + format(int(time.time() * 1000) << 12, "012x") + secrets.token_hex(7)
            payload["messageID"] = message_id
            await asyncio.to_thread(request.app.state.runtime.skills.record_use,
                                    path, session_id, message_id, managed, body.arguments)
        model = _model_choice(body.provider_id, body.model_id, request, body.variant)
        if model:
            payload["model"] = f"{model['providerID']}/{model['modelID']}"
        if body.agent:
            payload["agent"] = body.agent
        if body.variant:
            payload["variant"] = body.variant
        return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/command", payload)


def _agent_skill_allowed(agents: object, name: str, agent: str | None) -> bool:
    if not isinstance(agents, list):
        return True
    chosen = next((item for item in agents if item.get("name") == (agent or "build")), {})
    action = "allow"
    for rule in chosen.get("permission", []):
        if rule.get("permission") in ("skill", "*") and fnmatch.fnmatchcase(name, rule.get("pattern", "*")):
            action = rule.get("action", "allow")
    return action != "deny"


async def _agent_allows_skill(request: Request, path: str, name: str, agent: str | None) -> bool:
    return _agent_skill_allowed(await _opencode(request, path, "GET", "/agent"), name, agent)


async def _native_skill_catalog(request: Request, path: str) -> tuple[list, list]:
    commands, skills = await asyncio.gather(
        _opencode(request, path, "GET", "/command"),
        _opencode(request, path, "GET", "/skill"),
    )
    return (commands if isinstance(commands, list) else [], skills if isinstance(skills, list) else [])


def _native_skill_items(catalog: tuple[list, list]) -> list[dict]:
    commands, skills = catalog
    names = {item.get("name") for item in commands if item.get("source") == "skill"}
    return [{"id": item["name"], "name": item["name"], "description": item.get("description", ""),
             "path": str(Path(item["location"]).parent), "source": "native", "enabled": True}
            for item in skills if item.get("name") in names and isinstance(item.get("location"), str)
            and not item["location"].startswith("<") and item["name"] not in WORKSPACE_COMMANDS]


async def _native_managed_skill_names(
    request: Request, path: str, installed: list[dict], catalog: tuple[list, list] | None = None,
) -> set[str]:
    commands, skills = catalog if catalog is not None else await _native_skill_catalog(request, path)
    names = {item.get("name") for item in commands
             if isinstance(item, dict) and item.get("source") == "skill"}
    def matching_locations() -> set[str]:
        def canonical(value: str | Path) -> str:
            return os.path.normcase(str(Path(value).resolve()))

        locations = {item.get("name"): canonical(item["location"])
                     for item in skills if isinstance(item, dict) and isinstance(item.get("location"), str)}
        return {item["name"] for item in installed if item["name"] in names and
                locations.get(item["name"]) == canonical(Path(item["path"]) / "SKILL.md")}

    return await asyncio.to_thread(matching_locations)


@router.get("/workspace/projects/{project_id}/skills")
async def project_skills(project_id: str, request: Request, agent: str | None = None) -> dict:
    path = _project_path(request, project_id)
    installed = await asyncio.to_thread(request.app.state.runtime.skills.list)
    enabled = [item for item in installed["items"]
               if item["enabled"] and not item.get("error") and not item.get("conflict")]
    catalog = await _native_skill_catalog(request, path)
    names = await _native_managed_skill_names(request, path, enabled, catalog)
    installed_names = {item["name"] for item in installed["items"]}
    extra = [item for item in _native_skill_items(catalog) if item["name"] not in installed_names]
    for item in extra:
        if await asyncio.to_thread(request.app.state.runtime.skills.permission, item["name"]) != "deny":
            enabled.append(item)
            names.add(item["name"])
    if names:
        agents = await _opencode(request, path, "GET", "/agent")
        names = {name for name in names if _agent_skill_allowed(agents, name, agent)}
    return {"items": [item for item in enabled if item["name"] in names],
            "unavailable": [item["name"] for item in enabled if item["name"] not in names]}


class ShellBody(BaseModel):
    command: str = Field(min_length=1, max_length=100_000)
    agent: str = Field(default="build", min_length=1)
    provider_id: str | None = None
    model_id: str | None = None


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/shell")
async def run_shell(project_id: str, session_id: str, body: ShellBody, request: Request):
    path = _project_path(request, project_id)
    payload: dict = {"command": body.command, "agent": body.agent}
    model = _model_choice(body.provider_id, body.model_id, request, getattr(body, "variant", None))
    if model:
        payload["model"] = model
    return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/shell", payload)


@router.post("/workspace/projects/{project_id}/sessions/{session_id}/abort")
async def abort_session(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", f"/session/{_safe_id(session_id)}/abort", {})


@router.get("/workspace/projects/{project_id}/sessions/{session_id}/diff")
async def session_diff(project_id: str, session_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", f"/session/{_safe_id(session_id)}/diff")


@router.get("/workspace/projects/{project_id}/permissions")
async def list_permissions(project_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", "/permission")


@router.get("/workspace/projects/{project_id}/questions")
async def list_questions(project_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "GET", "/question")


class QuestionReplyBody(BaseModel):
    answers: list[list[str]] = Field(min_length=1, max_length=20)


@router.post("/workspace/projects/{project_id}/questions/{question_id}/reply")
async def reply_question(project_id: str, question_id: str, body: QuestionReplyBody, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", f"/question/{_safe_id(question_id)}/reply", {"answers": body.answers})


@router.post("/workspace/projects/{project_id}/questions/{question_id}/reject")
async def reject_question(project_id: str, question_id: str, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", f"/question/{_safe_id(question_id)}/reject", {})


class PermissionReplyBody(BaseModel):
    reply: str = Field(pattern="^(once|always|reject)$")


@router.post("/workspace/projects/{project_id}/permissions/{permission_id}/reply")
async def reply_permission(project_id: str, permission_id: str, body: PermissionReplyBody, request: Request):
    path = _project_path(request, project_id)
    return await _opencode(request, path, "POST", f"/permission/{_safe_id(permission_id)}/reply", {"reply": body.reply})


@router.get("/workspace/projects/{project_id}/events")
async def project_events(project_id: str, request: Request) -> StreamingResponse:
    path = _project_path(request, project_id)
    try:
        await request.app.state.runtime.workspace.ensure(path, request.app.state.runtime.config)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return StreamingResponse(
        request.app.state.runtime.workspace.events(path, request.app.state.runtime.config),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
