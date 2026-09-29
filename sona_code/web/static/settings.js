"use strict";
/* 设置页：上游管理 / 出站代理 / 记录设置 / 服务设置 + 连通性测试 + 保存 */

function renderSettings(view) {
  view.replaceChildren(el("div", { class: "loading", text: "加载中…" }));
  (async () => {
    let settings, meta;
    try {
      [settings, meta] = await Promise.all([api("settings"), api("meta")]);
    } catch (e) {
      view.replaceChildren(errorCard("加载设置失败：" + e.message, () => route()));
      return;
    }
    const cfg = settings.config;

    const restartBanner = el("div", { class: "banner banner-warn hidden" },
      el("b", { text: "⚠ 监听配置已变更：" }), "host / port / admin_prefix 属于监听配置，需重启服务后生效");
    const errBanner = el("div", { class: "banner banner-err hidden" });

    const metaItem = (label, value) => el("div", { class: "meta-item" },
      el("div", { class: "m-label", text: label }),
      el("div", { class: "m-value", title: value, text: value }));

    view.replaceChildren(
      el("section", { class: "card meta-bar" },
        metaItem("版本", "v" + (meta.version || "—")),
        metaItem("配置文件", settings.config_path || "—"),
        metaItem("记录目录", settings.records_dir || "—")),
      restartBanner,
      errBanner
    );

    /* ---------------- 工具 */
    const field = (label, control, hint) => el("div", { class: "field" },
      el("label", { class: "f-label", text: label }), control,
      hint ? el("div", { class: "f-hint", text: hint }) : null);

    function parseExtra(text) {
      const out = {};
      String(text || "").split("\n").forEach((line) => {
        line = line.trim();
        if (!line) return;
        const i = line.indexOf("=");
        if (i <= 0) return; // 非法行忽略
        out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
      });
      return out;
    }
    const splitCsv = (s) => String(s || "").split(",").map((x) => x.trim()).filter(Boolean);

    view.append(el("section", { class: "card" }, el("h2", { text: "模型配置" }),
      el("p", { text: "提供商、模型、密钥与调用路由已统一移至模型页面。" }),
      el("a", { class: "btn", href: "#/models", text: "管理模型" })));

    /* ---------------- 出站代理 */
    const proxyIn = el("input", { type: "text", value: cfg.outbound.proxy_url || "", placeholder: "http://127.0.0.1:7890 或 socks5://…", class: "mono" });
    view.append(el("section", { class: "card" }, el("h2", { text: "出站代理" }),
      field("proxy_url", proxyIn, "留空 = 直连；支持 http:// / https:// / socks5://")));

    /* ---------------- 记录设置 */
    const dirIn = el("input", { type: "text", value: cfg.recording.dir, class: "mono", placeholder: "~/.sona-code/records" });
    const rhIn = el("input", { type: "text", value: (cfg.recording.redact_headers || []).join(", "), class: "mono", placeholder: "authorization, x-api-key, …" });
    const shIn = el("input", { type: "text", value: (cfg.recording.session_id_headers || []).join(", "), class: "mono", placeholder: "x-deepseek-harness-session-id, x-session-id, …" });
    const maxIn = el("input", { type: "number", value: cfg.recording.max_capture_mb, min: "0.1", step: "0.5", style: "width:120px" });
    const retainIn = el("input", { type: "number", value: cfg.recording.retention_days != null ? cfg.recording.retention_days : 0, min: "0", step: "1", style: "width:120px" });
    const mkSwitch = (label, checked) => {
      const input = el("input", { type: "checkbox", checked });
      return [el("label", { class: "switch" }, input, el("span", { class: "slider" }), el("span", { class: "switch-label", text: label })), input];
    };
    const [redactSw, redactChk] = mkSwitch("脱敏敏感头（redact）", cfg.recording.redact);
    const [rrqSw, rrqChk] = mkSwitch("记录请求头", cfg.recording.record_request_headers);
    const [rspSw, rspChk] = mkSwitch("记录响应头", cfg.recording.record_response_headers);
    const [rchunkSw, rchunkChk] = mkSwitch("记录原始 SSE 分块（raw_chunks）", cfg.recording.record_raw_chunks);

    view.append(el("section", { class: "card" }, el("h2", { text: "记录设置" }),
      el("div", { class: "settings-grid" },
        el("div", { class: "field full" }, el("label", { class: "f-label", text: "记录目录 dir" }), dirIn,
          el("div", { class: "f-hint", text: "支持 ~ 展开；修改后立即对新记录生效" })),
        el("div", { class: "field full" }, el("label", { class: "f-label", text: "脱敏头列表 redact_headers（逗号分隔）" }), rhIn),
        el("div", { class: "field full" }, el("label", { class: "f-label", text: "会话归属头 session_id_headers（逗号分隔，按优先级）" }), shIn,
          el("div", { class: "f-hint", text: "请求命中列表中的头（大小写不敏感）即按头值聚合轨迹，优先于内容哈希；留空 = 仅按内容聚合" })),
        redactSw, rrqSw, rspSw, rchunkSw,
        el("div", { class: "field" }, el("label", { class: "f-label", text: "单次捕获上限 max_capture_mb" }), maxIn,
          el("div", { class: "f-hint", text: "请求/响应体超过该大小将被截断记录" })),
        el("div", { class: "field" }, el("label", { class: "f-label", text: "保留天数 retention_days" }), retainIn,
          el("div", { class: "f-hint", text: "0 = 永久保留；>0 时启动与每小时自动删除更早日期的记录（保存后生效）" })))));

    /* ---------------- 终端设置 */
    const tc = cfg.terminal || {};
    const cmdIn = el("input", { type: "text", value: tc.command != null ? tc.command : "opencode", class: "mono", placeholder: "opencode（PATH 中的命令名或完整路径）" });
    const cmdModeSel = el("select", null,
      el("option", { value: "auto", text: "随应用提供（缺失时回退 PATH）" }),
      el("option", { value: "custom", text: "自定义命令" }));
    cmdModeSel.value = tc.command_mode || ((tc.command && tc.command !== "opencode") ? "custom" : "auto");
    const syncCommandMode = () => { cmdIn.disabled = cmdModeSel.value !== "custom"; };
    cmdModeSel.addEventListener("change", syncCommandMode);
    syncCommandMode();
    const shellIn = el("input", { type: "text", value: tc.shell_command || "", class: "mono", placeholder: "留空 = 使用系统默认 shell" });
    const maxSessIn = el("input", { type: "number", value: tc.max_sessions != null ? tc.max_sessions : 8, min: "1", max: "64", style: "width:120px" });
    const sbIn = el("input", { type: "number", value: tc.scrollback_kb != null ? tc.scrollback_kb : 256, min: "0", max: "8192", style: "width:120px" });
    const envTa = el("textarea", { rows: "3", class: "mono", placeholder: "额外环境变量，每行一条：KEY=Value" });
    envTa.value = Object.entries(tc.inject_env || {}).map(([k, v]) => k + "=" + v).join("\n");
    const [termSw, termChk] = mkSwitch("启用 Web 终端（terminal.enabled）", tc.enabled !== false);
    view.append(el("section", { class: "card" },
      el("div", { class: "card-head-row" },
        el("h2", { text: "OpenCode 与终端" }),
        el("span", { class: "empty-hint", text: "工作区后台服务与旧版 Web 终端共用 OpenCode 配置" })),
      el("div", { class: "settings-grid" },
        termSw,
        el("div", { class: "field" }, el("label", { class: "f-label", text: "OpenCode 程序来源" }), cmdModeSel),
        el("div", { class: "field" }, el("label", { class: "f-label", text: "自定义 command" }), cmdIn,
          el("div", { class: "f-hint", text: "只有选择自定义时生效；可填完整路径或 PATH 命令" })),
        el("div", { class: "field" }, el("label", { class: "f-label", text: "shell 命令 shell_command" }), shellIn),
        el("div", { class: "field" }, el("label", { class: "f-label", text: "会话上限 max_sessions" }), maxSessIn),
        el("div", { class: "field" }, el("label", { class: "f-label", text: "回放缓冲 scrollback_kb" }), sbIn,
          el("div", { class: "f-hint", text: "重连浏览器时回放的输出大小（KB）" })),
        el("div", { class: "field full" }, el("label", { class: "f-label", text: "注入环境变量 inject_env" }), envTa))));

    /* ---------------- OpenCode 全局 JSONC 配置 */
    const configPath = el("div", { class: "empty-hint", text: "正在读取配置文件…" });
    const configEditor = el("textarea", { class: "mono opencode-editor", rows: "16", spellcheck: "false", disabled: true });
    const configMessage = el("div", { class: "empty-hint" });
    let configRevision = null;
    const reloadConfig = el("button", { type: "button", class: "btn", text: "重新读取", onclick: loadOpenCodeConfig });
    const saveConfig = el("button", { type: "button", class: "btn btn-primary", text: "保存 OpenCode 配置", disabled: true, onclick: async () => {
      saveConfig.disabled = true;
      configMessage.textContent = "保存中…";
      try {
        const result = await api("settings/opencode-config", {
          method: "PUT", body: { content: configEditor.value, revision: configRevision }, silent: true,
        });
        configRevision = result.revision;
        configMessage.textContent = "已保存；新建 OpenCode 会话时读取";
        toast("OpenCode 配置已保存", "ok");
      } catch (e) {
        configMessage.textContent = "保存失败：" + (e.detail ? format422(e.detail) : e.message);
      }
      saveConfig.disabled = false;
    } });
    async function loadOpenCodeConfig() {
      reloadConfig.disabled = true;
      try {
        const result = await api("settings/opencode-config", { silent: true });
        configPath.textContent = "当前文件：" + result.path;
        configEditor.value = result.content;
        configEditor.disabled = false;
        configRevision = result.revision;
        saveConfig.disabled = false;
        configMessage.textContent = "支持 JSONC 注释和末尾逗号；保存时保留原有格式，仅检查语法";
      } catch (e) {
        configMessage.textContent = "读取失败：" + e.message;
      }
      reloadConfig.disabled = false;
    }
    const importMessage = el("div", { class: "empty-hint", text: "正在检查可导入的 OpenCode 配置…" });
    const importBtn = el("button", { type: "button", class: "btn", text: "导入已有 OpenCode 配置", disabled: true, onclick: async () => {
      if (!window.confirm("将复制缺失的配置、扩展和凭据；已有文件不会被覆盖。继续吗？")) return;
      importBtn.disabled = true;
      importMessage.textContent = "导入中…";
      try {
        const result = await api("settings/opencode-import", { method: "POST", body: {}, silent: true });
        importMessage.textContent = `已导入 ${result.copied.length} 个文件，跳过 ${result.skipped.length} 个已有文件；新建 OpenCode 会话时生效。`;
        toast("OpenCode 配置导入完成", "ok");
        await loadOpenCodeConfig();
        await loadImportPreview();
      } catch (e) {
        importMessage.textContent = "导入失败：" + (e.detail ? format422(e.detail) : e.message);
        importBtn.disabled = false;
      }
    } });
    async function loadImportPreview() {
      try {
        const preview = await api("settings/opencode-import", { silent: true });
        importBtn.disabled = !preview.available || preview.copy_count < 1;
        if (!preview.available) {
          importMessage.textContent = "未检测到可导入的用户 OpenCode 配置。";
        } else {
          importMessage.textContent = `来源：${preview.source.config}；可导入 ${preview.copy_count} 个，已存在 ${preview.conflicts.length} 个（不覆盖）。`;
        }
      } catch (e) {
        importBtn.disabled = true;
        importMessage.textContent = "无法检查导入来源：" + e.message;
      }
    }
    view.append(el("section", { class: "card" },
      el("h2", { text: "OpenCode 全局配置" }),
      configPath,
      el("div", { class: "empty-hint", text: "直接编辑本应用隔离的 OpenCode 配置。项目内 opencode.json / .opencode 仍正常生效；代理模式会临时覆盖所选 provider 的接口地址及手动模型的输入能力。" }),
      configEditor,
      el("div", { class: "up-actions" }, reloadConfig, saveConfig), configMessage,
      el("div", { class: "settings-divider" }),
      el("div", { class: "up-actions" }, importBtn), importMessage));
    loadOpenCodeConfig();
    loadImportPreview();

    /* ---------------- 数据清理 */
    function fmtBytes(b) {
      const v = Number(b) || 0;
      if (v < 1024) return v + " B";
      if (v < 1048576) return (v / 1024).toFixed(1) + " KB";
      if (v < 1073741824) return (v / 1048576).toFixed(1) + " MB";
      return (v / 1073741824).toFixed(2) + " GB";
    }
    const cleanBox = el("div");
    async function loadStats() {
      cleanBox.replaceChildren(el("div", { class: "loading", text: "统计中…" }));
      let s;
      try {
        s = await api("records/stats", { silent: true });
      } catch (e) {
        cleanBox.replaceChildren(el("div", { class: "empty-hint", text: "读取存储统计失败：" + e.message }));
        return;
      }
      cleanBox.replaceChildren();
      if (!(s.dates || []).length) {
        cleanBox.append(emptyBox("暂无记录", "代理转发请求后，这里会展示存储占用"));
        return;
      }
      const tb = el("tbody");
      s.dates.forEach((d) => {
        const delBtn = el("button", { class: "btn btn-xs btn-danger", type: "button", text: "删除" });
        delBtn.addEventListener("click", async () => {
          if (!confirm(`确定删除 ${d.date} 的全部 ${d.calls} 条记录？此操作不可恢复。`)) return;
          delBtn.disabled = true;
          try {
            const r = await api("records/date/" + d.date, { method: "DELETE", silent: true });
            toast(`已删除 ${d.date}（${r.deleted} 条）`, "ok");
            loadStats();
          } catch (e) {
            toast("删除失败：" + (e.detail || e.message), "error");
            delBtn.disabled = false;
          }
        });
        tb.append(el("tr", null,
          el("td", { class: "mono", text: d.date }),
          el("td", { class: "mono num", text: fmtNum(d.calls) }),
          el("td", { class: "mono num", text: fmtNum(d.files) }),
          el("td", { class: "mono num", title: fmtNum(d.bytes), text: fmtBytes(d.bytes) }),
          el("td", null, delBtn)));
      });
      cleanBox.append(
        el("div", { class: "meta-line", text: `合计：${fmtNum(s.total_files)} 个文件 · ${fmtBytes(s.total_bytes)}` }),
        el("div", { class: "tbl-wrap" }, el("table", { class: "tbl" },
          el("thead", null, el("tr", null,
            el("th", { text: "日期" }), el("th", { text: "记录数", class: "num" }),
            el("th", { text: "文件数", class: "num" }), el("th", { text: "磁盘占用", class: "num" }),
            el("th", { text: "操作" }))),
          tb)));
    }
    loadStats();

    const purgeBtn = el("button", { class: "btn btn-danger", type: "button", text: "清空全部记录" });
    purgeBtn.addEventListener("click", async () => {
      if (!confirm("确定清空全部记录？所有日期的调用记录与索引都将被删除，不可恢复。")) return;
      if (!confirm("再次确认：真的要清空全部记录？")) return;
      purgeBtn.disabled = true;
      purgeBtn.textContent = "清空中…";
      try {
        const r = await api("records/all", { method: "DELETE", silent: true });
        toast(`已清空 ${r.dates} 个日期、${fmtNum(r.calls)} 条记录`, "ok");
        loadStats();
      } catch (e) {
        toast("清空失败：" + (e.detail || e.message), "error");
      }
      purgeBtn.disabled = false;
      purgeBtn.textContent = "清空全部记录";
    });

    const sweepBtn = el("button", { class: "btn", type: "button", text: "立即执行保留清理" });
    sweepBtn.addEventListener("click", async () => {
      sweepBtn.disabled = true;
      try {
        const r = await api("records/cleanup", { method: "POST", silent: true });
        if ((r.removed_dates || []).length) {
          toast(`已删除 ${r.deleted} 个过期日期：${r.removed_dates.join("、")}`, "ok");
        } else {
          toast(`没有过期记录（保留 ${r.retention_days} 天）`, "ok");
        }
        loadStats();
      } catch (e) {
        toast("清理失败：" + (e.detail || e.message), "error");
      }
      sweepBtn.disabled = false;
    });

    view.append(el("section", { class: "card", style: "border-left:3px solid var(--red)" },
      el("div", { class: "card-head-row" },
        el("h2", { text: "数据清理" }),
        el("span", { class: "empty-hint", text: "删除立即生效且不可恢复，请谨慎操作" })),
      cleanBox,
      el("div", { class: "up-actions" }, sweepBtn, purgeBtn)));

    /* ---------------- 服务设置 */
    const hostIn = el("input", { type: "text", value: cfg.server.host, class: "mono" });
    const portIn = el("input", { type: "number", value: cfg.server.port, style: { width: "120px" } });
    const prefixIn = el("input", { type: "text", value: cfg.server.admin_prefix, class: "mono" });
    view.append(el("section", { class: "card" },
      el("h2", null, "服务设置", el("span", { class: "tag-warn", text: "需重启生效" })),
      el("div", { class: "settings-grid" },
        el("div", { class: "field" }, el("label", { class: "f-label", text: "监听地址 host" }), hostIn),
        el("div", { class: "field" }, el("label", { class: "f-label", text: "端口 port" }), portIn),
        el("div", { class: "field" }, el("label", { class: "f-label", text: "管理路径前缀 admin_prefix" }), prefixIn,
          el("div", { class: "f-hint", text: "必须以 / 开头" })))));

    /* ---------------- 保存 */
    const saveBtn = el("button", { class: "btn btn-primary", text: "保存设置", onclick: save });
    async function save() {
      errBanner.classList.add("hidden");
      const body = {
        server: {
          host: hostIn.value.trim() || "127.0.0.1",
          port: parseInt(portIn.value, 10) || 0,
          admin_prefix: prefixIn.value.trim(),
        },
        outbound: { proxy_url: proxyIn.value.trim() },
        recording: {
          dir: dirIn.value.trim() || "~/.sona-code/records",
          redact: redactChk.checked,
          redact_headers: splitCsv(rhIn.value),
          session_id_headers: splitCsv(shIn.value),
          record_request_headers: rrqChk.checked,
          record_response_headers: rspChk.checked,
          record_raw_chunks: rchunkChk.checked,
          max_capture_mb: parseFloat(maxIn.value) || 20,
          retention_days: Math.max(0, parseInt(retainIn.value, 10) || 0),
        },
        terminal: {
          enabled: termChk.checked,
          command_mode: cmdModeSel.value,
          command: cmdIn.value.trim() || "opencode",
          shell_command: shellIn.value.trim(),
          max_sessions: Math.max(1, parseInt(maxSessIn.value, 10) || 8),
          scrollback_kb: Math.max(0, parseInt(sbIn.value, 10) || 0),
          inject_env: parseExtra(envTa.value),
        },
      };
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中…";
      try {
        const r = await api("settings", { method: "PUT", body });
        toast("已保存", "ok");
        restartBanner.classList.toggle("hidden", !r.restart_required);
        if (r.restart_required) restartBanner.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) {
        errBanner.classList.remove("hidden");
        errBanner.replaceChildren(el("b", { text: e.status === 422 ? "校验失败：" : "保存失败：" }),
          e.status === 422 || e.detail
            ? el("pre", { class: "pre-block", text: e.detail ? format422(e.detail) : e.message })
            : String(e.message));
      }
      saveBtn.disabled = false;
      saveBtn.textContent = "保存设置";
    }
    view.append(el("div", { class: "save-bar" }, saveBtn));
  })();
}
