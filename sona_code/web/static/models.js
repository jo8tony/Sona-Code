"use strict";

async function saveModelSource(source, updates, view) {
  try {
    await api("models/source", { method: "PUT", body: {
      source: updates.source || source.source,
      environment: updates.environment || source.environment,
      uat_url: updates.uat_url === undefined ? source.uat_url : updates.uat_url,
      prod_url: updates.prod_url === undefined ? source.prod_url : updates.prod_url,
    }, silent: true });
    await renderModels(view);
  } catch (error) {
    toast(error.detail || error.message, "error");
  }
}

function modelSourceTabs(view, source) {
  const labels = [["sona", "Sona 网站"], ["custom", "用户自己配置"], ["native", "本地 OpenCode"]];
  return el("section", { class: "card models-top" },
    el("h1", { text: "模型来源" }),
    el("div", { class: "inline-controls" }, ...labels.map(([value, label]) =>
      el("button", { type: "button", class: "btn" + (source.source === value ? " btn-primary" : ""),
        "aria-pressed": String(source.source === value), text: label,
        onclick: () => { if (source.source !== value) saveModelSource(source, { source: value }, view); } }))));
}

async function renderSonaModels(view, source, tabs) {
  const error = el("p", { class: "banner banner-err", hidden: true });
  const environment = el("select", { "aria-label": "网站环境" },
    el("option", { value: "prod", text: "生产" }), el("option", { value: "uat", text: "UAT" }));
  environment.value = source.environment;
  environment.addEventListener("change", () => saveModelSource(source, { environment: environment.value }, view));
  const uatUrl = el("input", { type: "url", value: source.uat_url, placeholder: "https://..." });
  const prodUrl = el("input", { type: "url", value: source.prod_url, placeholder: "https://..." });
  const urls = el("section", { class: "card" },
    el("h2", { text: "网站环境" }),
    el("label", { class: "field" }, el("span", { class: "f-label", text: "当前环境" }), environment),
    el("label", { class: "field" }, el("span", { class: "f-label", text: "UAT 前端地址" }), uatUrl),
    el("label", { class: "field" }, el("span", { class: "f-label", text: "生产前端地址" }), prodUrl),
    el("button", { type: "button", class: "btn", text: "保存地址", onclick: () =>
      saveModelSource(source, { uat_url: uatUrl.value.trim(), prod_url: prodUrl.value.trim() }, view) }));
  const account = el("section", { class: "card" }, el("h2", { text: "网站登录" }),
    el("p", { text: source.connected
      ? `已登录：${source.user?.userName || source.user?.userId || "当前用户"}`
      : "当前环境尚未登录。登录后将读取你创建的场景和已获批订阅。" }));
  account.append(el("p", { class: "f-hint", text: "网站凭据仅保存在本次 App 运行期间；重启后需要重新登录。已有 OpenCode 终端切换来源后需要重启。" }));
  async function action(path, button) {
    if (button?.disabled) return;
    const label = button?.textContent;
    error.hidden = true;
    if (button) {
      button.disabled = true; button.setAttribute("aria-busy", "true");
      if (path === "models/sona/refresh") button.textContent = "正在刷新…";
    }
    try {
      const result = await api(path, { method: "POST", silent: true });
      await renderModels(view);
      if (path === "models/sona/refresh") toast(`模型已刷新，共 ${result.providers.reduce((sum, provider) => sum + provider.models.length, 0)} 个模型`);
    } catch (cause) {
      error.hidden = false; error.textContent = cause.detail || cause.message;
      toast(error.textContent, "error");
    }
    finally {
      if (button) { button.disabled = false; button.textContent = label; button.removeAttribute("aria-busy"); }
    }
  }
  if (source.connected) {
    account.append(el("div", { class: "inline-controls" },
      el("button", { type: "button", class: "btn", text: "刷新模型", onclick: event => action("models/sona/refresh", event.currentTarget) }),
      el("button", { type: "button", class: "btn", text: "退出登录", onclick: event => action("models/sona/logout", event.currentTarget) })));
  } else {
    account.append(el("button", { type: "button", class: "btn btn-primary", text: "在浏览器中登录", onclick: async () => {
      error.hidden = true;
      try {
        const { url } = await api("models/sona/login/start", { method: "POST", silent: true });
        if (window.__TAURI__?.core?.invoke) await window.__TAURI__.core.invoke("open_website_login", { url });
        else window.open(url, "_blank", "noopener");
        const expires = Date.now() + 5 * 60 * 1000;
        const timer = setInterval(async () => {
          if (!view.isConnected || location.hash !== "#/models" || Date.now() > expires) { clearInterval(timer); return; }
          try {
            const latest = await api("models/source", { silent: true });
            if (latest.source !== "sona") { clearInterval(timer); return; }
            if (latest.connected && latest.environment === source.environment) {
              clearInterval(timer);
              await renderModels(view);
            }
          } catch (_) { /* Retry while browser login is pending. */ }
        }, 2000);
      } catch (cause) { error.hidden = false; error.textContent = cause.detail || cause.message; }
    } }));
  }
  const catalog = el("section", { class: "card" }, el("h2", { text: "已订阅模型" }));
  if (source.updated_at) catalog.append(el("p", { class: "f-hint", text: `最近刷新：${new Date(source.updated_at * 1000).toLocaleString()}` }));
  if (!source.connected) catalog.append(el("p", { class: "empty-hint", text: "登录后显示模型。" }));
  else if (!source.providers.length) catalog.append(el("p", { class: "empty-hint", text: "当前环境没有可用的场景订阅。" }));
  else for (const provider of source.providers) catalog.append(el("div", { class: "models-model" },
    el("strong", { text: provider.name }),
    el("p", { text: provider.models.map((model) => model.name).join("、") })));
  view.replaceChildren(tabs, error, urls, account, catalog);
}

async function renderNativeModels(view, tabs) {
  const panel = el("section", { class: "card" }, el("h2", { text: "本地 OpenCode 模型" }),
    el("p", { class: "f-hint", text: "模型和凭据沿用本机 OpenCode 配置；在工作区模型选择器中设置提供商 Key。" }));
  view.replaceChildren(tabs, panel);
  try {
    const projects = await api("workspace/projects", { silent: true });
    if (!projects.items?.length) {
      panel.append(el("p", { class: "empty-hint", text: "请先在工作区添加项目。" }));
      return;
    }
    const select = el("select", { "aria-label": "项目" }, ...projects.items.map((project) =>
      el("option", { value: project.id, text: project.name })));
    const list = el("div");
    const load = async () => {
      list.replaceChildren(el("p", { class: "empty-hint", text: "正在读取 OpenCode 模型…" }));
      try {
        const data = await api(`workspace/projects/${encodeURIComponent(select.value)}/models`, { silent: true });
        list.replaceChildren();
        for (const provider of data.providers || []) list.append(el("div", { class: "models-model" },
          el("strong", { text: provider.name }),
          el("p", { text: Object.values(provider.models || {}).map((model) => model.name || model.id).join("、") })));
        if (!data.providers?.length) list.append(el("p", { class: "empty-hint", text: "没有可用的本地模型。" }));
      } catch (cause) { list.replaceChildren(el("p", { class: "banner banner-err", text: cause.detail || cause.message })); }
    };
    select.addEventListener("change", load);
    panel.append(el("label", { class: "field" }, el("span", { class: "f-label", text: "项目" }), select), list);
    await load();
  } catch (cause) { panel.append(el("p", { class: "banner banner-err", text: cause.detail || cause.message })); }
}

async function renderModels(view) {
  if (typeof workspaceModelCache !== "undefined") workspaceModelCache.clear();
  view.replaceChildren(el("div", { class: "loading", text: "加载模型配置…" }));
  let source;
  try { source = await api("models/source", { silent: true }); }
  catch (error) { view.replaceChildren(errorCard("加载模型来源失败：" + error.message, () => renderModels(view))); return; }
  if (location.hash !== "#/models") return;
  const tabs = modelSourceTabs(view, source);
  if (source.source === "sona") { await renderSonaModels(view, source, tabs); return; }
  if (source.source === "native") { await renderNativeModels(view, tabs); return; }
  let draft;
  try { draft = await api("models/config"); }
  catch (error) { view.replaceChildren(errorCard("加载模型配置失败：" + error.message, () => renderModels(view))); return; }
  if (location.hash !== "#/models") return;
  let selected = 0;
  let keyRefreshers = [];
  const savedNames = new Set(draft.providers.map((p) => p.name));
  const savedModels = new Set(draft.providers.flatMap((p) => p.models.map((m) => p.name + "\u0000" + m.id)));
  const errorBox = el("div", { class: "banner banner-err", role: "alert", hidden: true });
  const note = el("p", { class: "empty-hint", role: "status", text: "配置对所有项目共用；修改后点击保存。已有 OpenCode 终端需要重启。" });
  const providerList = el("div", { class: "models-provider-list" });
  const editor = el("section", { class: "card models-editor" });
  const defaultSelect = el("select", { id: "models-default", "aria-label": "应用默认模型" });
  const externalDefault = el("select", { "aria-label": "外部代理默认上游" });
  const nativeCheck = el("input", { type: "checkbox", checked: draft.show_native_models });
  nativeCheck.addEventListener("change", () => { draft.show_native_models = nativeCheck.checked; changed(); });
  defaultSelect.addEventListener("change", () => {
    const [provider, model] = defaultSelect.value.split("\u0000");
    draft.default_model = provider && model ? { provider, model } : null;
    changed();
  });
  externalDefault.addEventListener("change", () => { draft.default_upstream = externalDefault.value; changed(); });
  const save = el("button", { type: "button", class: "btn btn-primary", text: "保存模型配置", onclick: saveCatalog });
  const field = (label, control, hint) => el("label", { class: "field" }, el("span", { class: "f-label", text: label }), control,
    hint ? el("span", { class: "f-hint", text: hint }) : null);
  function changed() { note.textContent = "有未保存的修改；工作区有任务运行时不能保存，表单内容会保留。"; }
  function textInput(obj, key, placeholder = "", type = "text", options = {}) {
    const input = el("input", { type, value: obj[key] == null ? "" : String(obj[key]), placeholder, ...options });
    input.addEventListener("input", () => {
      obj[key] = type === "number" ? (input.value ? Number(input.value) : null) : input.value.trim();
      changed();
      if (["id", "name", "display_name"].includes(key)) { renderList(); updateDefaults(); }
    });
    return input;
  }
  function toggle(obj, key, label, after) {
    const check = el("input", { type: "checkbox", checked: obj[key] });
    check.addEventListener("change", () => { obj[key] = check.checked; changed(); if (after) after(); });
    return el("label", { class: "model-toggle" }, check, label);
  }
  function keyControl(obj, provider) {
    const status = el("small", { class: "f-hint" });
    const refresh = () => {
      const own = Boolean(obj.api_key);
      const inherited = provider && Boolean(provider.api_key);
      status.textContent = own ? "已配置 Key" : inherited ? "继承提供商 Key" : "未配置 Key";
    };
    const input = el("input", { type: "password", autocomplete: "new-password",
      placeholder: provider ? "留空继承提供商 Key" : "输入 API Key" });
    input.value = obj.api_key || "";
    input.addEventListener("input", () => {
      obj.api_key = input.value;
      keyRefreshers.forEach((fn) => fn()); changed();
    });
    const clear = el("button", { type: "button", class: "btn btn-xs", text: "清除", onclick: () => {
      obj.api_key = ""; input.value = ""; keyRefreshers.forEach((fn) => fn()); changed();
    } });
    const show = el("button", { type: "button", class: "btn btn-xs", text: "显示", "aria-pressed": "false", onclick: () => {
      input.type = input.type === "password" ? "text" : "password";
      show.textContent = input.type === "password" ? "显示" : "隐藏";
      show.setAttribute("aria-pressed", String(input.type === "text"));
    } });
    keyRefreshers.push(refresh);
    refresh();
    return el("div", null, el("div", { class: "inline-controls" }, input, show, clear), status);
  }
  function apiSelect(obj, inherit) {
    const select = el("select", { "aria-label": "接口类型" },
      inherit ? el("option", { value: "", text: "继承提供商" }) : null,
      el("option", { value: "chat_completions", text: "Chat Completions" }),
      el("option", { value: "responses", text: "Responses" }));
    select.value = obj.api_type || "";
    select.addEventListener("change", () => { obj.api_type = select.value || null; changed(); });
    return select;
  }
  function updateDefaults() {
    const value = draft.default_model ? draft.default_model.provider + "\u0000" + draft.default_model.model : "";
    defaultSelect.replaceChildren(el("option", { value: "", text: "未指定：使用第一个应用模型" }),
      ...draft.providers.flatMap((p) => p.models.filter((m) => p.name && m.id).map((m) =>
        el("option", { value: p.name + "\u0000" + m.id, text: `${p.display_name || p.name} / ${m.display_name || m.id}` }))));
    if (Array.from(defaultSelect.options).some((o) => o.value === value)) defaultSelect.value = value;
    else { defaultSelect.value = ""; draft.default_model = null; }
    externalDefault.replaceChildren(...draft.providers.filter((p) => p.name).map((p) =>
      el("option", { value: p.name, text: p.display_name || p.name })));
    if (!draft.providers.some((p) => p.name === draft.default_upstream)) draft.default_upstream = draft.providers[0]?.name || "";
    externalDefault.value = draft.default_upstream;
  }
  function renderList() {
    providerList.replaceChildren(...draft.providers.map((provider, index) =>
      el("button", { type: "button", class: "models-provider" + (selected === index ? " selected" : ""), onclick: () => {
        selected = index; renderList(); renderEditor();
      } }, el("strong", { text: provider.display_name || provider.name || "新提供商" }),
      el("small", { text: `${provider.models.length} 个模型 · ${provider.route_through_proxy ? "代理记录" : "直连"}` }))));
    if (!draft.providers.length) providerList.append(el("p", { class: "empty-hint", text: "还没有提供商" }));
  }
  function modelCard(provider, model) {
    const saved = savedModels.has(provider.name + "\u0000" + model.id);
    const details = el("details", { class: "models-model", open: !saved });
    const summary = el("summary", null, el("strong", { text: model.display_name || model.id || "新模型" }),
      el("small", { text: model.context_length ? `${model.context_length.toLocaleString()} 上下文` : "待补充上下文和输出上限" }));
    const remove = el("button", { type: "button", class: "btn btn-xs btn-danger", text: "删除模型", onclick: () => {
      if (saved && !confirm(`删除模型 ${model.display_name || model.id}？保存后生效。`)) return;
      provider.models.splice(provider.models.indexOf(model), 1); changed(); updateDefaults(); renderList(); renderEditor();
    } });
    const reasoningBox = el("div", { class: "models-reasoning" });
    const renderReasoning = () => {
      reasoningBox.replaceChildren();
      if (!model.reasoning) return;
      const configurable = el("input", { type: "checkbox", checked: Boolean(model.reasoning_efforts?.length) });
      configurable.addEventListener("change", () => {
        model.reasoning_efforts = configurable.checked ? ["low", "medium", "high"] : [];
        model.default_effort = null; changed(); renderReasoning();
      });
      reasoningBox.append(el("label", { class: "model-toggle" }, configurable, "可配置思考强度"));
      if (!configurable.checked) return;
      const choices = el("div", { class: "model-capabilities" });
      const defaultEffort = el("select", { "aria-label": "默认思考强度" });
      const refreshEffort = () => {
        defaultEffort.replaceChildren(el("option", { value: "", text: "不指定，使用服务默认值" }),
          ...model.reasoning_efforts.map((value) => el("option", { value, text: value })));
        if (!model.reasoning_efforts.includes(model.default_effort)) model.default_effort = null;
        defaultEffort.value = model.default_effort || "";
      };
      for (const effort of ["none", "minimal", "low", "medium", "high", "xhigh", "max"]) {
        const check = el("input", { type: "checkbox", checked: model.reasoning_efforts.includes(effort) });
        check.addEventListener("change", () => {
          model.reasoning_efforts = check.checked ? [...model.reasoning_efforts, effort] : model.reasoning_efforts.filter((e) => e !== effort);
          refreshEffort(); changed();
        });
        choices.append(el("label", { class: "model-toggle" }, check, effort));
      }
      defaultEffort.addEventListener("change", () => { model.default_effort = defaultEffort.value || null; changed(); });
      refreshEffort(); reasoningBox.append(choices, field("默认思考强度", defaultEffort));
    };
    const modalities = el("div", { class: "model-capabilities" });
    for (const [id, label] of [["image", "输入图片"], ["audio", "音频"], ["video", "视频"], ["pdf", "PDF"]]) {
      const check = el("input", { type: "checkbox", checked: model.input_modalities.includes(id) });
      check.addEventListener("change", () => {
        model.input_modalities = check.checked ? [...model.input_modalities, id] : model.input_modalities.filter((v) => v !== id); changed();
      });
      modalities.append(el("label", { class: "model-toggle" }, check, label));
    }
    details.append(summary, el("div", { class: "settings-grid" },
      field("模型名称", textInput(model, "id", "上游实际 model 值", "text", { readOnly: saved }), "保存后固定标识；更换标识请添加新模型"),
      field("显示名称", textInput(model, "display_name", "可留空")),
      field("最大上下文（Token）", textInput(model, "context_length", "按服务实际填写", "number", { min: 1, step: 1 })),
      field("最大输出（Token）", textInput(model, "output_length", "按服务实际填写", "number", { min: 1, step: 1 })),
      field("模型独立 API Key", keyControl(model, provider)), field("接口类型", apiSelect(model, true))),
      modalities, el("div", { class: "model-capabilities" },
        toggle(model, "reasoning", "支持思考", () => {
          if (!model.reasoning) { model.reasoning_efforts = []; model.default_effort = null; }
          renderReasoning();
        }), toggle(model, "tool_call", "支持工具调用")),
      el("p", { class: "f-hint", text: "只开启服务实际支持的能力；不支持工具调用的模型不适合需要工具的工作区任务。" }), reasoningBox, remove);
    renderReasoning(); return details;
  }
  function renderEditor() {
    keyRefreshers = [];
    const provider = draft.providers[selected];
    editor.replaceChildren();
    if (!provider) { editor.append(el("h2", { text: "添加模型提供商" }), el("p", { class: "empty-hint", text: "先添加提供商，再配置模型。" })); return; }
    const name = textInput(provider, "name", "如 company", "text", { readOnly: savedNames.has(provider.name), id: "models-provider-name" });
    const routeHint = el("p", { class: "f-hint", text: provider.route_through_proxy ? "模型调用进入本地调用记录和轨迹。" : "本次调用不会进入本地调用记录与代理轨迹；工作区对话和工具活动仍然可见。" });
    editor.append(el("div", { class: "card-head-row" }, el("h2", { text: "提供商配置" }),
      el("button", { type: "button", class: "btn btn-xs btn-danger", text: "删除提供商", onclick: () => {
        if (!confirm(`删除提供商 ${provider.display_name || provider.name || "新提供商"} 及其模型？保存后生效。`)) return;
        draft.providers.splice(selected, 1); selected = Math.max(0, selected - 1); changed(); renderList(); updateDefaults(); renderEditor();
      } })), el("div", { class: "settings-grid" },
      field("提供商名称", name, "唯一标识，保存后固定"), field("显示名称", textInput(provider, "display_name", "可留空")),
      field("Base URL", textInput(provider, "base_url", "https://example.com/v1", "url"), "完整 API 根地址，不自动添加 /v1"),
      field("API Key", keyControl(provider)), field("接口类型", apiSelect(provider, false))),
      toggle(provider, "route_through_proxy", "通过本地代理访问", () => {
        renderList(); routeHint.textContent = provider.route_through_proxy ? "模型调用进入本地调用记录和轨迹。" : "本次调用不会进入本地调用记录与代理轨迹；工作区对话和工具活动仍然可见。";
      }), routeHint);
    const headers = el("textarea", { rows: 3, value: "" });
    headers.value = Object.entries(provider.extra_headers || {}).map(([k, v]) => `${k}=${v}`).join("\n");
    headers.addEventListener("input", () => {
      provider.extra_headers = {};
      for (const line of headers.value.split("\n")) { const i = line.indexOf("="); if (i > 0) provider.extra_headers[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
      changed();
    });
    const strategy = el("select", null, el("option", { value: "keep", text: "透传客户端凭据" }), el("option", { value: "replace", text: "使用提供商 Key" }));
    strategy.value = provider.key_strategy;
    strategy.addEventListener("change", () => { provider.key_strategy = strategy.value; changed(); });
    editor.append(el("details", { class: "models-advanced" }, el("summary", { text: "高级与外部代理设置" }),
      field("额外请求头", headers, "每行 Header=Value；[REDACTED] 表示保留已有敏感值"),
      field("外部代理密钥策略", strategy, "仅用于外部客户端；工作区始终遵循模型 Key → 提供商 Key → 无 Key")),
      el("div", { class: "card-head-row" }, el("h2", { text: "模型" }),
        el("button", { type: "button", class: "btn", text: "＋ 添加模型", onclick: () => {
          provider.models.push({ id: "", display_name: "", api_type: null, context_length: null, output_length: null,
            input_modalities: [], reasoning: false, reasoning_efforts: [], default_effort: null, tool_call: true });
          changed(); renderList(); renderEditor();
        } })), ...provider.models.map((model) => modelCard(provider, model)));
    if (!provider.models.length) editor.append(el("p", { class: "empty-hint", text: "尚未添加模型。" }));
  }
  async function saveCatalog() {
    save.disabled = true; errorBox.hidden = true;
    try {
      const result = await api("models/config", { method: "PUT", body: draft, silent: true });
      draft = result;
      savedNames.clear(); savedModels.clear();
      draft.providers.forEach((p) => { savedNames.add(p.name); p.models.forEach((m) => savedModels.add(p.name + "\u0000" + m.id)); });
      note.textContent = "模型配置已保存；工作区后台服务会自动重新连接。" + (result.terminal_restart_required ? " 请重启已有 OpenCode 终端。" : "");
      renderList(); updateDefaults(); renderEditor(); toast("模型配置已保存", "ok");
    } catch (error) {
      errorBox.hidden = false;
      errorBox.textContent = (typeof error.detail === "string" ? error.detail : error.detail ? format422(error.detail) : error.message) + "；未保存的表单已保留。";
    } finally { save.disabled = false; }
  }
  view.replaceChildren(tabs, el("section", { class: "card models-top" }, el("h1", { text: "用户自己配置的模型" }),
    el("div", { class: "settings-grid" }, field("应用默认模型", defaultSelect),
      field("外部代理默认上游", externalDefault))),
    errorBox, el("div", { class: "models-layout" }, el("aside", { class: "card models-providers" },
      el("button", { type: "button", class: "btn", text: "＋ 添加提供商", onclick: () => {
        draft.providers.push({ name: "", display_name: "", base_url: "", api_type: "chat_completions", route_through_proxy: true,
          extra_headers: {}, key_strategy: "keep", models: [] });
        selected = draft.providers.length - 1; changed(); renderList(); renderEditor();
        editor.querySelector("#models-provider-name")?.focus();
      } }), providerList), editor), el("div", { class: "save-bar" }, note,
      el("button", { type: "button", class: "btn", text: "重新加载", onclick: () => {
        if (confirm("重新加载会丢弃未保存修改，继续吗？")) renderModels(view);
      } }), save));
  renderList(); updateDefaults(); renderEditor();
}
