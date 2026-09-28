"""Application model catalog, native config compiler and secret-free views."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from llm_api_proxy_recorder.config import AppConfig, UpstreamConfig, UpstreamModelConfig

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def route_token(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def native_provider_id(name: str) -> str:
    return "llmpr-" + route_token(name)


def sensitive_header(name: str) -> bool:
    return bool(re.search(r"authorization|cookie|key|token|secret", name, re.I))


def public_config(cfg: AppConfig) -> dict:
    data = cfg.model_dump()
    for provider in data["upstreams"]:
        provider["has_api_key"] = bool(provider.pop("api_key"))
        provider["extra_headers"] = {k: "[REDACTED]" if sensitive_header(k) else v
                                     for k, v in provider["extra_headers"].items()}
        for model in provider["models"]:
            model["has_api_key"] = bool(model.pop("api_key"))
            model["key_source"] = ("model" if model["has_api_key"] else
                                   "provider" if provider["has_api_key"] else "none")
    data["terminal"]["inject_env"] = {k: "[REDACTED]" if sensitive_header(k) else v
                                      for k, v in data["terminal"]["inject_env"].items()}
    return data


def catalog_revision(cfg: AppConfig) -> str:
    # Includes secrets, but the digest never exposes them and detects key-only edits.
    data = {"upstreams": [p.model_dump() for p in cfg.upstreams],
            "default_upstream": cfg.default_upstream, "model_settings": cfg.model_settings.model_dump()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def catalog_view(cfg: AppConfig) -> dict:
    """Editable catalog with saved keys; general settings remain redacted."""
    data = public_config(cfg)
    for provider, upstream in zip(data["upstreams"], cfg.upstreams):
        provider["api_key"] = upstream.api_key
        for model, configured in zip(provider["models"], upstream.models):
            model["api_key"] = configured.api_key
    return {"revision": catalog_revision(cfg), "providers": data["upstreams"],
            "default_upstream": cfg.default_upstream, **data["model_settings"]}


def merge_providers(items: list[dict], cfg: AppConfig, *, require_limits: bool = True) -> list[dict]:
    old = {p.name: p for p in cfg.upstreams}
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(422, "提供商必须是对象")
        item = dict(item)
        if item.get("source", "custom") != "custom":
            raise HTTPException(422, "网站模型不能在本地配置中编辑")
        if not isinstance(item.get("name"), str):
            raise HTTPException(422, "提供商名称必须是字符串")
        prior = old.get(item.get("name"))
        if not prior and not re.fullmatch(r"[A-Za-z0-9_-]+", str(item.get("name", ""))):
            raise HTTPException(422, "提供商名称仅支持字母、数字、下划线和连字符")
        if "api_key" not in item:
            item["api_key"] = prior.api_key if prior else ""
        if item.get("api_key") is None:
            raise HTTPException(422, "清除密钥请使用空字符串")
        if "extra_headers" not in item:
            item["extra_headers"] = dict(prior.extra_headers) if prior else {}
        elif isinstance(item["extra_headers"], dict):
            item["extra_headers"] = {k: (prior.extra_headers.get(k, "") if prior else "")
                                     if v == "[REDACTED]" else v
                                     for k, v in item["extra_headers"].items()}
        previous = {m.id: m for m in prior.models} if prior else {}
        models = []
        if not isinstance(item.get("models", []), list):
            raise HTTPException(422, "模型必须是列表")
        for model in item.get("models", []):
            if not isinstance(model, dict):
                raise HTTPException(422, "模型必须是对象")
            model = dict(model)
            if not isinstance(model.get("id"), str):
                raise HTTPException(422, "模型名称必须是字符串")
            before = previous.get(model.get("id"))
            if "api_key" not in model:
                model["api_key"] = before.api_key if before else ""
            # Unchanged legacy models may retain unknown limits. Any edit requires both.
            editable = {k: v for k, v in model.items() if k not in {"has_api_key", "key_source"}}
            if require_limits and (before is None or editable != before.model_dump()):
                if not model.get("context_length") or not model.get("output_length"):
                    raise HTTPException(422, "新增或编辑模型时必须填写最大上下文和最大输出长度")
            models.append(model)
        item["models"] = models
        result.append(item)
    return result


def validate_config(data: dict) -> AppConfig:
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_input=False, include_context=False, include_url=False)) from None


def model_sdk(model: UpstreamModelConfig, provider: UpstreamConfig) -> str:
    return "@ai-sdk/openai" if (model.api_type or provider.api_type) == "responses" else "@ai-sdk/openai-compatible"


def compile_providers(cfg: AppConfig) -> dict:
    providers = {}
    for provider in cfg.upstreams:
        if not provider.models:
            continue
        models = {}
        for model in provider.models:
            base = provider.base_url.rstrip("/")
            if provider.route_through_proxy:
                base = (f"http://127.0.0.1:{cfg.server.port}/managed/"
                        f"{route_token(provider.name)}/{route_token(model.id)}")
            key = model.api_key or provider.api_key
            # apiKey belongs to the SDK/provider; model headers override it per model.
            # The local recorder injects the real key, so it never enters native metadata.
            headers = {} if provider.route_through_proxy else {
                k: v for k, v in provider.extra_headers.items() if k.lower() not in {"authorization", "x-api-key", "api-key"}}
            headers["Authorization"] = f"Bearer {key}" if key and not provider.route_through_proxy else ""
            entry: dict[str, Any] = {
                "name": model.display_name or model.id,
                "provider": {"api": base, "npm": model_sdk(model, provider)},
                "headers": headers, "attachment": bool(model.input_modalities),
                "modalities": {"input": ["text", *model.input_modalities], "output": ["text"]},
                "reasoning": model.reasoning, "tool_call": model.tool_call,
                # Disable automatically inferred variants; only explicit choices survive.
                "variants": {effort: ({"reasoningEffort": effort} if effort in model.reasoning_efforts
                                      else {"disabled": True}) for effort in EFFORTS},
                # OpenAI accepts null to suppress native GPT-5 effort defaults.
                # OpenAI-compatible rejects null before sending the HTTP request,
                # so unspecified effort must be omitted for that SDK.
                "options": ({"reasoningEffort": model.default_effort}
                            if model.default_effort is not None or model_sdk(model, provider) == "@ai-sdk/openai"
                            else {}),
            }
            limits = {k: v for k, v in {"context": model.context_length, "output": model.output_length}.items()
                      if v is not None}
            if limits:
                entry["limit"] = limits
            models[model.id] = entry
        providers[native_provider_id(provider.name)] = {
            "name": provider.display_name or provider.name, "env": [],
            "npm": "@ai-sdk/openai-compatible", "options": {"apiKey": ""}, "models": models,
        }
    return providers


def application_catalog(cfg: AppConfig) -> list[dict]:
    compiled = compile_providers(cfg)
    result = []
    for provider in cfg.upstreams:
        pid = native_provider_id(provider.name)
        if pid not in compiled:
            continue
        models = {}
        for model in provider.models:
            entry = compiled[pid]["models"][model.id]
            models[model.id] = {
                "id": model.id, "name": entry["name"], "limit": entry.get("limit", {}),
                "variants": {e: {} for e in model.reasoning_efforts},
                "capabilities": {"reasoning": model.reasoning, "toolcall": model.tool_call,
                                 "input": {kind: kind in ["text", *model.input_modalities]
                                           for kind in ("text", "image", "pdf", "audio", "video")}},
                "source": provider.source, "route_through_proxy": provider.route_through_proxy,
                "key_source": "model" if model.api_key else "provider" if provider.api_key else "none",
                "api_type": model.api_type or provider.api_type,
            }
        result.append({"id": pid, "name": compiled[pid]["name"], "source": provider.source,
                       "route_through_proxy": provider.route_through_proxy, "models": models})
    return result


def public_native_catalog(data: Any) -> list[dict]:
    result = []
    for provider in data.get("providers", []) if isinstance(data, dict) else []:
        if not isinstance(provider, dict) or str(provider.get("id", "")).startswith("llmpr-"):
            continue
        # Allowlist fields: native options, headers and arbitrary variants can contain keys.
        models = {}
        for mid, model in (provider.get("models") or {}).items():
            if not isinstance(model, dict):
                continue
            models[mid] = {k: model[k] for k in ("id", "name", "limit", "capabilities") if k in model}
            models[mid].update(source="native", variants={e: {} for e in model.get("variants", {})})
        result.append({"id": provider.get("id"), "name": provider.get("name"), "models": models,
                       "source": "native"})
    return result
