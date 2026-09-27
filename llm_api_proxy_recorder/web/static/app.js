"use strict";
/* 核心逻辑：工具函数、fetch 封装、hash 路由、仪表盘、调用列表、调用详情 */

/* ============================================================ 工具 */
const $ = (sel, root) => (root || document).querySelector(sel);
const EL_SVG_TAGS = new Set(["svg", "path", "circle", "rect", "line", "polyline", "polygon", "g", "defs", "linearGradient", "stop", "ellipse"]);

function el(tag, attrs, ...children) {
  const isSvg = EL_SVG_TAGS.has(tag);
  const node = isSvg ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined) continue;
      if (k === "class") { if (isSvg) node.setAttribute("class", v); else node.className = v; }
      else if (k === "text") node.textContent = v;
      else if (k === "value") node.value = v;
      else if (k === "checked") node.checked = !!v;
      else if (["disabled", "hidden", "open", "readOnly"].includes(k)) node[k] = !!v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, String(v));
    }
  }
  _appendKids(node, children);
  return node;
}
function _appendKids(node, kids) {
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === true) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
}

/* 数字格式化：千分位 / 紧凑 token / 自适应时长 / 百分比 / 日期时间 */
function fmtNum(n) {
  const v = Number(n);
  if (n === null || n === undefined || n === "" || !isFinite(v)) return "—";
  return v.toLocaleString("zh-CN");
}
function fmtTokens(n) {
  const v = Number(n);
  if (n === null || n === undefined || n === "" || !isFinite(v)) return "—";
  for (const [div, suf] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
    if (Math.abs(v) >= div) {
      const r = v / div;
      return (r >= 100 ? r.toFixed(0) : r.toFixed(1)) + suf;
    }
  }
  return String(v);
}
function fmtMs(ms) {
  const v = Number(ms);
  if (ms === null || ms === undefined || ms === "" || !isFinite(v)) return "—";
  if (v < 1000) return Math.round(v) + " ms";
  if (v < 60000) return (v / 1000).toFixed(2) + " s";
  const m = Math.floor(v / 60000);
  return `${m}m ${Math.round((v % 60000) / 1000)}s`;
}
function fmtPercent(x) {
  const v = Number(x);
  if (x === null || x === undefined || !isFinite(v)) return "—";
  const p = v * 100;
  return (p >= 10 ? p.toFixed(1) : p.toFixed(2)).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") + "%";
}
function fmtTime(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : String(iso);
}
function _pad2(n) { return String(n).padStart(2, "0"); }
function fmtDateLocal(d) {
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`;
}
function lastNDates(n) {
  const out = [];
  const d = new Date();
  for (let i = 0; i < n; i++) {
    out.push(fmtDateLocal(d));
    d.setDate(d.getDate() - 1);
  }
  return out; // 新→旧
}

/* ============================================================ fetch 封装 */
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET" };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers = { "Content-Type": "application/json" };
  }
  let resp;
  try {
    resp = await fetch("api/" + path, init);
  } catch (e) {
    if (!opts.silent) toast("网络请求失败：" + e.message, "error");
    const err = new Error(e.message);
    err.network = true;
    throw err;
  }
  if (!resp.ok) {
    let detail = null;
    try {
      const j = await resp.json();
      if (j && j.detail !== undefined) detail = j.detail;
    } catch (_) { /* 响应非 JSON */ }
    const err = new Error(`HTTP ${resp.status}`);
    err.status = resp.status;
    err.detail = detail;
    if (!opts.silent) toast(`请求失败（HTTP ${resp.status}）`, "error");
    throw err;
  }
  return resp.json();
}

function format422(detail) {
  if (Array.isArray(detail)) {
    return detail
      .map((d) => `· ${((d.loc || []).slice(1).join(".") || "(root)")}：${d.msg || ""}`)
      .join("\n");
  }
  if (typeof detail === "string") return detail;
  try { return JSON.stringify(detail); } catch (_) { return String(detail); }
}

/* ============================================================ toast / 通用组件 */
function toast(msg, type) {
  const box = $("#toast-box");
  const t = el("div", { class: "toast " + (type || ""), text: msg });
  box.append(t);
  setTimeout(() => {
    t.classList.add("out");
    setTimeout(() => t.remove(), 320);
  }, type === "error" ? 5000 : 2500);
}

function errorCard(msg, retryFn) {
  const c = el("div", { class: "card", style: { borderLeft: "3px solid var(--red)" } },
    el("div", { text: msg }));
  if (retryFn) c.append(el("div", { style: { marginTop: "10px" } }, el("button", { class: "btn", text: "重试", onclick: retryFn })));
  return c;
}

function emptyBox(text, sub) {
  const box = el("div", { class: "empty" }, el("span", { class: "icon", text: "∅" }), el("div", { text: text }));
  if (sub) box.append(el("div", { class: "empty-hint", text: sub }));
  return box;
}

/* 状态徽标：2xx 绿 / 4xx 橙 / 5xx 红 / client_aborted 灰 */
function statusCell(row, large) {
  const status = row.status, code = row.status_code;
  let cls, text, tip;
  if (status === "client_aborted") { cls = "b-aborted"; text = "中断"; tip = "客户端中断"; }
  else if (code !== null && code !== undefined) {
    cls = code >= 500 ? "b-err" : code >= 400 ? "b-warn" : code >= 200 && code < 300 ? "b-ok" : "b-dim";
    text = String(code);
    tip = `HTTP ${code}`;
  } else if (status === "error") { cls = "b-err"; text = "error"; tip = "错误"; }
  else if (status === "ok") { cls = "b-ok"; text = "ok"; tip = "成功"; }
  else { cls = "b-dim"; text = "—"; tip = ""; }
  return el("span", { class: "badge " + cls + (large ? " badge-lg" : ""), title: tip, text });
}

/* 调用列表表格（仪表盘"最近调用"与列表页共用） */
function buildCallsTable(rows) {
  const heads = [
    ["时间", ""], ["模型", ""], ["路径", ""], ["状态", ""],
    ["耗时", "num"], ["输入 tok", "num"], ["输出 tok", "num"], ["上游", ""], ["操作", ""],
  ];
  const thead = el("thead", null, el("tr", null,
    ...heads.map(([h, cls]) => el("th", { text: h, class: cls }))));
  const tb = el("tbody");
  if (!rows.length) {
    tb.append(el("tr", null, el("td", { colspan: "9" }, emptyBox("暂无调用记录", "代理转发请求后，这里会展示调用明细"))));
  }
  for (const r of rows) {
    const href = "#/calls/" + encodeURIComponent(r.id || "");
    tb.append(el("tr", {
      class: "row-link",
      onclick: () => { location.hash = href; },
    },
      el("td", { class: "mono dim", title: r.started_at || "", text: fmtTime(r.started_at) }),
      el("td", { class: "mono", title: r.model || "", text: r.model || "—" }),
      el("td", { class: "mono dim path-cell", title: r.path || "", text: r.path || "—" }),
      el("td", null, statusCell(r)),
      el("td", { class: "mono num", text: fmtMs(r.duration_ms) }),
      el("td", { class: "mono num", title: fmtNum(r.prompt_tokens), text: fmtTokens(r.prompt_tokens) }),
      el("td", { class: "mono num", title: fmtNum(r.completion_tokens), text: fmtTokens(r.completion_tokens) }),
      el("td", { class: "dim", title: r.upstream_name || "", text: r.upstream_name || "—" }),
      el("td", null, el("a", { class: "btn btn-xs", href, text: "详情" }))
    ));
  }
  return el("div", { class: "tbl-wrap" }, el("table", { class: "tbl" }, thead, tb));
}

// 菜单模式仅保留在当前页面生命周期中，重新打开默认使用用户模式。
let adminMenusVisible = false;
function updateAdminMenus() {
  document.querySelectorAll("[data-admin-menu]").forEach((item) => {
    item.hidden = !adminMenusVisible;
  });
}
function handleMenuCommand(text) {
  if (text !== "#admin#" && text !== "#user#") return false;
  adminMenusVisible = text === "#admin#";
  updateAdminMenus();
  toast(adminMenusVisible ? "已显示管理菜单" : "已隐藏管理菜单", "ok");
  return true;
}

/* ============================================================ 路由 */
let cleanups = [];
function addCleanup(fn) { cleanups.push(fn); }
function runCleanups() {
  cleanups.forEach((fn) => { try { fn(); } catch (_) {} });
  cleanups = [];
}

const routes = [
  { re: /^#\/workspace$/, nav: "workspace", render: (view) => renderWorkspace(view) },
  { re: /^#\/models$/, nav: "models", render: (view) => renderModels(view) },
  { re: /^#\/skills$/, nav: "skills", render: (view) => renderSkills(view) },
  { re: /^#\/dashboard$/, nav: "dashboard", render: (view) => renderDashboard(view) },
  { re: /^#\/calls$/, nav: "calls", render: (view) => renderCalls(view) },
  { re: /^#\/calls\/([^?]+)(?:\?(.*))?$/, nav: "calls", render: (view, m) => renderCallDetail(view, decodeURIComponent(m[1]), new URLSearchParams(m[2]).get("from") === "workspace") },
  { re: /^#\/trajectory$/, nav: "trajectory", render: (view) => renderTrajectoryList(view) },
  { re: /^#\/trajectory\/(.+)$/, nav: "trajectory", render: (view, m) => renderTrajectorySession(view, decodeURIComponent(m[1])) },
  { re: /^#\/terminal$/, nav: "terminal", render: (view) => renderTerminal(view) },
  { re: /^#\/settings$/, nav: "settings", render: (view) => renderSettings(view) },
];

function setNav(name) {
  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === name);
    if (a.dataset.nav === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function route() {
  let hash = location.hash || "#/workspace";
  if (hash === "#/preferences") {
    hash = adminMenusVisible ? "#/settings" : "#/skills";
    history.replaceState(null, "", location.pathname + location.search + hash);
  }
  runCleanups();
  const view = $("#view");
  view.replaceChildren(el("div", { class: "loading", text: "加载中…" }));
  for (const r of routes) {
    const m = hash.match(r.re);
    if (m) {
      document.body.classList.toggle("workspace-route", r.nav === "workspace");
      setNav(r.nav);
      updateAdminMenus();
      document.title = "Sona Code · " + ({ workspace: "工作区", models: "模型", skills: "技能", dashboard: "仪表盘", calls: "调用列表", trajectory: "轨迹", terminal: "终端", settings: "设置" }[r.nav] || "");
      r.render(view, m);
      return;
    }
  }
  location.hash = "#/workspace";
}

/* ============================================================ 仪表盘 */
function statCard(label, value, cls, title) {
  return el("div", { class: "stat-card " + (cls || "") },
    el("div", { class: "stat-label", text: label }),
    el("div", { class: "stat-value" + (cls === "bad" ? " bad" : ""), title: title || "", text: value }));
}

async function renderDashboard(view) {
  let alive = true;
  const draw = async (silent) => {
    let data;
    try {
      data = await api("overview", { silent });
    } catch (e) {
      if (!alive) return;
      if (!silent) view.replaceChildren(errorCard("加载概览失败：" + (e.detail ? format422(e.detail) : e.message), () => route()));
      return;
    }
    if (!alive) return;
    view.replaceChildren();

    // 统计卡片
    view.append(el("div", { class: "stat-grid" },
      statCard("总调用", fmtNum(data.total_calls)),
      statCard("输入 token", fmtNum(data.total_prompt_tokens), "tok"),
      statCard("输出 token", fmtNum(data.total_completion_tokens), "tok"),
      statCard("平均耗时", data.avg_duration_ms == null ? "—" : fmtMs(data.avg_duration_ms)),
      statCard("错误率", fmtPercent(data.error_rate), data.error_rate > 0 ? "bad" : "")
    ));

    // 14 天趋势
    const chartCard = el("section", { class: "card" },
      el("div", { class: "card-head-row" },
        el("h2", { text: "近 14 天趋势" }),
        el("span", { class: "empty-hint", text: "每 30 秒自动刷新" })));
    const chartBox = el("div", { class: "chart-box" });
    drawDailyChart(chartBox, data.by_day || []);
    chartCard.append(chartBox);
    view.append(chartCard);

    // 按模型汇总
    const models = data.by_model || [];
    const modelCard = el("section", { class: "card" }, el("h2", { text: "按模型汇总" }));
    if (models.length) {
      const thead = el("thead", null, el("tr", null,
        el("th", { text: "模型" }), el("th", { text: "调用次数", class: "num" }),
        el("th", { text: "输入 token", class: "num" }), el("th", { text: "输出 token", class: "num" }),
        el("th", { text: "token 合计", class: "num" })));
      const tb = el("tbody");
      models
        .slice()
        .sort((a, b) => b.calls - a.calls)
        .forEach((m) => {
          tb.append(el("tr", null,
            el("td", { class: "mono", text: m.model || "（未知）" }),
            el("td", { class: "mono num", text: fmtNum(m.calls) }),
            el("td", { class: "mono num", title: fmtNum(m.prompt_tokens), text: fmtTokens(m.prompt_tokens) }),
            el("td", { class: "mono num", title: fmtNum(m.completion_tokens), text: fmtTokens(m.completion_tokens) }),
            el("td", { class: "mono num", text: fmtNum((m.prompt_tokens || 0) + (m.completion_tokens || 0)) })));
        });
      modelCard.append(el("div", { class: "tbl-wrap" }, el("table", { class: "tbl" }, thead, tb)));
    } else {
      modelCard.append(emptyBox("暂无数据"));
    }
    view.append(modelCard);

    // 最近调用
    const recentCard = el("section", { class: "card" },
      el("div", { class: "card-head-row" },
        el("h2", { text: "最近调用" }),
        el("a", { href: "#/calls", text: "查看全部 →" })));
    recentCard.append(buildCallsTable(data.recent || []));
    view.append(recentCard);
  };

  await draw(false);
  const t = setInterval(() => {
    if (alive && document.visibilityState === "visible") draw(true);
  }, 30000);
  addCleanup(() => { alive = false; clearInterval(t); });
}

/* ============================================================ 调用列表 */
let callsState = null; // 会话内保留过滤条件

async function renderCalls(view) {
  if (!callsState) {
    callsState = { date: lastNDates(14)[0], model: "", status: "", q: "", page: 1, page_size: 50, auto: true, total: 0 };
  }
  const st = callsState;

  // ---- 过滤栏（仅构建一次，自动刷新不重建，避免打断输入）
  const dateSel = el("select");
  dateSel.append(el("option", { value: "all", text: "全部日期" }));
  const today = lastNDates(14)[0];
  lastNDates(14).forEach((d) => dateSel.append(el("option", { value: d, text: d + (d === today ? "（今天）" : "") })));
  dateSel.value = st.date;
  dateSel.addEventListener("change", apply);

  const modelInput = el("input", { type: "text", placeholder: "精确匹配模型名", value: st.model });
  modelInput.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
  modelInput.addEventListener("change", apply);

  const statusSel = el("select", null,
    el("option", { value: "", text: "全部状态" }),
    el("option", { value: "ok", text: "成功" }),
    el("option", { value: "error", text: "错误" }),
    el("option", { value: "client_aborted", text: "客户端中断" }));
  statusSel.value = st.status;
  statusSel.addEventListener("change", apply);

  const qInput = el("input", { type: "text", placeholder: "搜索 id / 路径 / 模型", value: st.q, class: "w-220" });
  qInput.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
  qInput.addEventListener("change", apply);

  const autoChk = el("input", { type: "checkbox", checked: st.auto });
  autoChk.addEventListener("change", () => { st.auto = autoChk.checked; });

  function apply() {
    st.date = dateSel.value;
    st.model = modelInput.value.trim();
    st.status = statusSel.value;
    st.q = qInput.value.trim();
    st.page = 1;
    refresh(false);
  }

  const item = (label, ctrl) => el("div", { class: "filter-item" }, el("span", { class: "f-label", text: label }), ctrl);
  const tableBox = el("div");
  const pagerBox = el("div");

  view.replaceChildren(
    el("div", { class: "card filter-bar" },
      item("日期", dateSel),
      item("模型", modelInput),
      item("状态", statusSel),
      item("关键字", qInput),
      el("button", { class: "btn", text: "应用", onclick: apply }),
      el("span", { class: "filter-spacer" }),
      el("label", { class: "switch" }, autoChk, el("span", { class: "slider" }), el("span", { class: "switch-label", text: "5s 自动刷新" }))
    ),
    tableBox,
    pagerBox
  );

  async function refresh(silent) {
    const params = new URLSearchParams();
    params.set("date", st.date || "all");
    if (st.model) params.set("model", st.model);
    if (st.status) params.set("status", st.status);
    if (st.q) params.set("q", st.q);
    params.set("page", String(st.page));
    params.set("page_size", String(st.page_size));
    let data;
    try {
      data = await api("calls?" + params.toString(), { silent });
    } catch (e) {
      if (!silent) tableBox.replaceChildren(errorCard("加载调用列表失败：" + e.message, () => refresh(false)));
      return;
    }
    st.total = data.total || 0;
    tableBox.replaceChildren(el("section", { class: "card" }, buildCallsTable(data.items || [])));
    // 分页
    const pages = Math.max(1, Math.ceil(st.total / st.page_size));
    pagerBox.replaceChildren(el("div", { class: "pager" },
      el("button", { class: "btn btn-xs", text: "上一页", disabled: st.page <= 1, onclick: () => { st.page--; refresh(false); } }),
      el("span", { class: "pager-info", text: `第 ${st.page} / ${pages} 页 · 共 ${fmtNum(st.total)} 条` }),
      el("button", { class: "btn btn-xs", text: "下一页", disabled: st.page >= pages, onclick: () => { st.page++; refresh(false); } })
    ));
  }

  await refresh(false);
  const t = setInterval(() => {
    // 仅第一页且页面可见时自动刷新
    if (st.auto && st.page === 1 && document.visibilityState === "visible") refresh(true);
  }, 5000);
  addCleanup(() => clearInterval(t));
}

/* ============================================================ JSON 查看器（简易语法高亮） */
function jsonViewer(value) {
  let text = null;
  try { text = JSON.stringify(value); } catch (_) { text = null; }
  if (text !== null && text.length > 1e6) return bigTextBlock(text);
  return el("div", { class: "json-wrap" }, el("div", { class: "json" }, jsonNode(value, 0)));
}

function jsonNode(v, depth) {
  const pad = depth * 14;
  if (v === null) return el("span", { class: "j-null", text: "null" });
  const t = typeof v;
  if (t === "string") return el("span", { class: "j-str", text: JSON.stringify(v) });
  if (t === "number") return el("span", { class: "j-num", text: String(v) });
  if (t === "boolean") return el("span", { class: "j-bool", text: String(v) });
  const isArr = Array.isArray(v);
  const entries = isArr ? v.map((x) => [null, x]) : Object.entries(v);
  if (!entries.length) return el("span", { class: "j-punc", text: isArr ? "[]" : "{}" });
  const wrap = el("span", { class: "j-coll" });
  wrap.append(el("div", { class: "j-line" }, el("span", { class: "j-punc", text: isArr ? "[" : "{" })));
  entries.forEach(([k, val], i) => {
    const line = el("div", { class: "j-line" });
    line.style.paddingLeft = pad + 14 + "px";
    if (!isArr) {
      line.append(el("span", { class: "j-key", text: JSON.stringify(k) }), el("span", { class: "j-punc", text: ": " }));
    }
    line.append(jsonNode(val, depth + 1));
    if (i < entries.length - 1) line.append(el("span", { class: "j-punc", text: "," }));
    wrap.append(line);
  });
  const closing = el("div", { class: "j-line" });
  closing.style.paddingLeft = pad + "px";
  closing.append(el("span", { class: "j-punc", text: isArr ? "]" : "}" }));
  wrap.append(closing);
  return wrap;
}

/* 超大文本（>1MB）截断展示 */
function bigTextBlock(text) {
  return el("div", null,
    el("div", { class: "banner banner-warn" }, el("b", { text: "内容过大：" }), `共 ${fmtNum(text.length)} 字符，仅展示前 1MB`),
    el("pre", { class: "pre-block", text: text.slice(0, 1000000) + "\n…（已截断）" }));
}

function bodyViewer(body, truncated) {
  const parts = [];
  if (truncated) parts.push(el("div", { class: "banner banner-warn" }, el("b", { text: "记录时已截断：" }), "body 超过 max_capture_mb，仅记录了前面部分"));
  if (body === null || body === undefined) {
    parts.push(el("div", { class: "empty-hint", text: "（无请求体）" }));
  } else if (typeof body === "string") {
    const s = body.trim();
    if (s.startsWith("{") || s.startsWith("[")) {
      try {
        parts.push(el("div", { class: "empty-hint", text: "（从字符串解析为 JSON）" }));
        parts.push(jsonViewer(JSON.parse(s)));
        return el("div", null, ...parts);
      } catch (_) { /* 非 JSON，按原文展示 */ }
    }
    parts.push(el("pre", { class: "pre-block", text: body }));
  } else {
    parts.push(jsonViewer(body));
  }
  return el("div", null, ...parts);
}

/* ============================================================ 响应内容可读化 */
function _tryParse(s) {
  try { return JSON.parse(s); } catch (_) { return undefined; }
}
function _block(title, text, cls) {
  return el("div", { class: "msg-block" },
    el("span", { class: "lbl", text: title }),
    el("pre", { class: "pre-block " + (cls || ""), text: String(text) }));
}

function renderContent(content) {
  if (content === null || content === undefined || content === "") {
    return el("div", { class: "empty-hint", text: "（无响应内容）" });
  }
  if (typeof content === "string") {
    const s = content.trim();
    if (s.startsWith("{") || s.startsWith("[")) {
      const parsed = _tryParse(s);
      if (parsed !== undefined) return jsonViewer(parsed);
    }
    return el("pre", { class: "pre-block", text: content });
  }
  if (content && typeof content === "object" && Array.isArray(content.output)) {
    const box = el("div", { class: "content-box" });
    content.output.forEach((item) => {
      if (item.type === "message") (item.content || []).forEach((part) => {
        if (part.text || part.refusal) box.append(_block(part.refusal ? "拒绝原因" : "回复内容", part.text || part.refusal, "pre-content"));
      });
      else if (item.type === "reasoning") (item.summary || []).forEach((part) => {
        if (part.text) box.append(_block("思考摘要", part.text, "pre-reason"));
      });
      else if (item.type === "function_call") box.append(_block(`工具调用 ${item.name || ""}`, item.arguments || ""));
      else box.append(jsonViewer(item));
    });
    return box.childNodes.length ? box : jsonViewer(content);
  }
  const box = el("div", { class: "content-box" });
  if (content && typeof content === "object" && Array.isArray(content.choices)) {
    // OpenAI 风格：优先展示 message 内容
    if (content.id || content.model || content.created) {
      box.append(el("div", { class: "meta-line", text: `id ${content.id || "—"} · model ${content.model || "—"} · created ${content.created || "—"}` }));
    }
    content.choices.forEach((ch, i) => {
      const c = el("div", { class: "choice" });
      c.append(el("div", { class: "choice-head" },
        el("span", { class: "chip", text: "choice " + i }),
        ch.finish_reason ? el("span", { class: "chip chip-dim", text: "finish: " + ch.finish_reason }) : null));
      const msg = ch.message || ch.delta || {};
      if (msg.role) c.append(el("div", { class: "msg-line" }, el("span", { class: "chip chip-role", text: msg.role })));
      if (msg.reasoning_content) c.append(_block("思考过程（reasoning_content）", msg.reasoning_content, "pre-reason"));
      if (msg.content !== null && msg.content !== undefined) {
        if (typeof msg.content === "string") {
          if (msg.content) c.append(_block("回复内容（content）", msg.content, "pre-content"));
        } else {
          c.append(el("div", { class: "msg-line" }, el("span", { class: "lbl", text: "content" })), jsonViewer(msg.content));
        }
      }
      if (Array.isArray(msg.tool_calls)) {
        for (const tc of msg.tool_calls) {
          const fn = tc.function || {};
          const args = typeof fn.arguments === "string" ? _tryParse(fn.arguments) : fn.arguments;
          c.append(el("div", { class: "toolcall" },
            el("div", { class: "msg-line" },
              el("span", { class: "chip chip-tool", text: "tool_call " + (tc.type || "function") }),
              el("span", { class: "mono", text: fn.name || "" }),
              tc.id ? el("span", { class: "mono dim", text: " " + tc.id }) : null),
            args === undefined && fn.arguments ? el("pre", { class: "pre-block", text: fn.arguments }) : jsonViewer(args)));
        }
      }
      box.append(c);
    });
    if (content.usage) box.append(el("div", { class: "meta-line", text: "usage: " + JSON.stringify(content.usage) }));
    if (content.error) {
      box.append(el("div", { class: "banner banner-err", style: { marginTop: "10px" } }, el("b", { text: "响应携带错误：" }), jsonViewer(content.error)));
    }
    if (!box.childNodes.length) box.append(jsonViewer(content));
    return box;
  }
  return jsonViewer(content);
}

/* ============================================================ 调用详情 */
function sumItem(label, valueNode) {
  return el("div", { class: "sum-item" },
    el("div", { class: "s-label", text: label }),
    el("div", { class: "s-value" }, valueNode));
}

function headersTable(headers, emptyText) {
  if (!headers || !Object.keys(headers).length) {
    return el("div", { class: "empty-hint", text: emptyText || "（未记录 headers）" });
  }
  const tb = el("tbody");
  Object.entries(headers).forEach(([k, v]) => {
    tb.append(el("tr", null, el("td", { class: "mono", style: { width: "220px" }, text: k }), el("td", { class: "mono", text: String(v) })));
  });
  return el("div", { class: "tbl-wrap" }, el("table", { class: "tbl headers-tbl" }, tb));
}

function rawChunksDetails(chunks) {
  const det = el("details", { class: "raw-chunks" });
  det.append(el("summary", null, `原始 SSE 分块（${chunks.length} 块）`));
  const capped = chunks.slice(0, 200);
  capped.forEach((c, i) => {
    det.append(el("div", { class: "chunk" },
      el("div", { class: "chunk-i mono", text: "#" + i }),
      el("pre", { class: "pre-block", text: String(c) })));
  });
  if (chunks.length > capped.length) {
    det.append(el("div", { class: "empty-hint", text: `共 ${chunks.length} 块，仅展示前 200 块` }));
  }
  return det;
}

async function renderCallDetail(view, id, fromWorkspace = false) {
  const returnHref = fromWorkspace ? "#/workspace" : "#/calls";
  let rec;
  try {
    rec = await api("calls/" + encodeURIComponent(id));
  } catch (e) {
    view.replaceChildren(errorCard(e.status === 404 ? `记录 ${id} 不存在（可能已被清理）` : "加载详情失败：" + e.message, () => route()));
    return;
  }
  view.replaceChildren();

  // 顶部：返回 + id + 删除
  const delBtn = el("button", { class: "btn btn-xs btn-danger", type: "button", text: "删除记录" });
  delBtn.addEventListener("click", async () => {
    if (!confirm(`确定删除记录 ${id}？此操作不可恢复。`)) return;
    delBtn.disabled = true;
    try {
      await api("calls/" + encodeURIComponent(id), { method: "DELETE", silent: true });
      toast("已删除记录", "ok");
      location.hash = returnHref;
    } catch (e) {
      toast("删除失败：" + (e.detail || e.message), "error");
      delBtn.disabled = false;
    }
  });
  view.append(el("div", { class: "detail-top" },
    el("a", { class: "btn btn-ghost", href: returnHref, text: fromWorkspace ? "← 返回活动" : "← 返回列表" }),
    el("span", { class: "mono dim", text: rec.id }),
    el("span", { class: "filter-spacer" }),
    delBtn));

  // 概要条
  const resp = rec.response || {};
  const usage = rec.usage || {};
  const tokensTitle = `输入 ${fmtNum(usage.prompt_tokens)} / 输出 ${fmtNum(usage.completion_tokens)} / 合计 ${fmtNum(usage.total_tokens)}` +
    (usage.cached_tokens != null ? ` / 缓存 ${fmtNum(usage.cached_tokens)}` : "");
  view.append(el("div", { class: "sum-bar" },
    sumItem("状态", statusCell(rec, true)),
    sumItem("模型", rec.model || "—"),
    sumItem("耗时", fmtMs(rec.duration_ms)),
    sumItem("TTFT", fmtMs(resp.ttft_ms)),
    sumItem("分块数", resp.chunk_count != null ? String(resp.chunk_count) : "—"),
    sumItem("解码速率", resp.decode_tokens_per_sec != null ? resp.decode_tokens_per_sec.toFixed(1) + " tok/s" : "—"),
    sumItem("tokens（输入/输出）", el("span", { title: tokensTitle, text: `${fmtTokens(usage.prompt_tokens)} / ${fmtTokens(usage.completion_tokens)}` })),
    sumItem("流式", rec.stream ? "是" : "否"),
    sumItem("上游", el("span", { title: rec.upstream_url || "", text: rec.upstream_name || "—" })),
    sumItem("开始时间", fmtTime(rec.started_at)),
    sumItem("结束时间", fmtTime(rec.finished_at))
  ));

  // 错误与用量
  if (rec.error) {
    view.append(el("section", { class: "card", style: { borderLeft: "3px solid var(--red)" } },
      el("h2", { text: "错误" }),
      el("div", { class: "msg-line" }, el("span", { class: "badge b-err", text: rec.error.type || "error" })),
      el("pre", { class: "pre-block", text: rec.error.message || "" })));
  }
  if (rec.usage) {
    view.append(el("section", { class: "card" },
      el("h2", { text: "Token 用量" }),
      el("div", { class: "usage-grid" },
        el("div", { class: "u-item" }, el("div", { class: "u-label", text: "输入 prompt" }), el("div", { class: "u-value", text: fmtNum(usage.prompt_tokens) })),
        el("div", { class: "u-item" }, el("div", { class: "u-label", text: "输出 completion" }), el("div", { class: "u-value", text: fmtNum(usage.completion_tokens) })),
        el("div", { class: "u-item" }, el("div", { class: "u-label", text: "合计 total" }), el("div", { class: "u-value", text: fmtNum(usage.total_tokens) })),
        el("div", { class: "u-item" }, el("div", { class: "u-label", text: "缓存 cached" }), el("div", { class: "u-value", text: fmtNum(usage.cached_tokens) })))));
  }

  // 请求区
  const req = rec.request || {};
  const query = Object.entries(req.query || {});
  let qs = "";
  if (query.length) {
    qs = "?" + query.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&");
  }
  const reqCard = el("section", { class: "card" }, el("h2", { text: "请求" }));
  reqCard.append(el("div", { class: "msg-line" },
    el("span", { class: "badge b-ok", text: req.method || "GET" }),
    el("span", { class: "mono", text: (req.path || "/") + qs })));
  reqCard.append(el("h3", { text: "请求头（已脱敏）" }));
  reqCard.append(headersTable(req.headers, "（未记录请求头）"));
  reqCard.append(el("h3", { text: "请求体" }));
  reqCard.append(bodyViewer(req.body, req.body_truncated));
  if (rec.client && rec.client.user_agent) {
    reqCard.append(el("div", { class: "meta-line", text: "user-agent: " + rec.client.user_agent }));
  }
  view.append(reqCard);

  // 响应区
  const resCard = el("section", { class: "card" },
    el("div", { class: "card-head-row" },
      el("h2", { text: "响应" }),
      el("span", { class: "empty-hint", text: `首字节 ${fmtMs(resp.first_byte_ms)} · 解码耗时 ${resp.decode_ms != null ? resp.decode_ms.toFixed(1) + " ms" : "—"}` })));
  resCard.append(el("div", { class: "msg-line" }, statusCell(rec, true), resp.body_truncated ? el("span", { class: "chip chip-dim", text: "记录时已截断" }) : null));
  resCard.append(el("h3", { text: "响应头" }));
  resCard.append(headersTable(resp.headers, "（未记录响应头）"));
  resCard.append(el("h3", { text: "响应内容（组装后）" }));
  resCard.append(renderContent(resp.content));
  if (Array.isArray(resp.raw_chunks) && resp.raw_chunks.length) {
    resCard.append(rawChunksDetails(resp.raw_chunks));
  }
  view.append(resCard);
}

/* ============================================================ 启动 */
// 首次进入时写入实际路由，供异步页面判断当前所在视图。
if (!location.hash) history.replaceState(null, "", location.pathname + location.search + "#/workspace");
window.addEventListener("hashchange", route);
route();

// 设置侧栏版本号
api("meta", { silent: true })
  .then((m) => { $("#meta-version").textContent = "v" + (m.version || ""); })
  .catch(() => {});
