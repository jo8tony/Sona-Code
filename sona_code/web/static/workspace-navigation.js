"use strict";

// Index rendered user rows so internal continuation/compaction messages never
// become navigation targets. Native message IDs survive polling and streaming.
function workspacePromptEntries(messages, rows) {
  const targets = new Map(Array.from(rows).filter(row => row.workspaceUserMessageId)
    .map(row => [row.workspaceUserMessageId, row]));
  return messages.filter(message => message.info?.role === "user" && targets.has(message.info.id))
    .map(message => {
      const skill = message.skillUse;
      const text = skill ? `/${skill.name}${skill.arguments ? ` ${skill.arguments}` : ""}` :
        (message.parts || []).filter(part => part.type === "text" && !part.synthetic &&
          !workspaceDirectoryReference(part)).map(part => part.text || "").join(" ").trim();
      const files = (message.parts || []).filter(part => part.type === "file")
        .map(part => part.filename || "附件");
      return {id: message.info.id, node: targets.get(message.info.id),
        summary: text || files.join("、") || "消息内容暂不可用", created: message.info.time?.created};
    });
}

function createWorkspaceNavigation(view, {onJump, onLatest, isPinned}) {
  const frame = view.querySelector("#wsp-chat-body");
  const scroll = view.querySelector("#wsp-scroll");
  const content = view.querySelector("#wsp-content");
  const rail = view.querySelector("#wsp-prompt-rail");
  const trigger = view.querySelector("#wsp-prompt-trigger");
  const menu = view.querySelector("#wsp-prompt-menu");
  const tooltip = view.querySelector("#wsp-prompt-tooltip");
  const latest = view.querySelector("#wsp-latest");
  let entries = [];
  let controls = new Map();
  let key = "";
  let activeId = "";
  let tooltipId = "";
  let frameId = null;
  let flashTimer = null;
  let flashed = null;
  let disposed = false;

  const excerpt = value => {
    const characters = Array.from(value.replace(/\s+/g, " ").trim());
    return characters.slice(0, 100).join("") + (characters.length > 100 ? "…" : "");
  };
  const time = created => Number.isFinite(created) ? new Date(created).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }) : "";

  function hideTooltip() {
    tooltip.hidden = true;
    tooltipId = "";
  }

  function closeMenu(returnFocus = false) {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (returnFocus) trigger.focus({preventScroll: true});
  }

  function showTooltip(id) {
    const entry = entries.find(item => item.id === id);
    const control = controls.get(id);
    if (!entry || !control || rail.hidden) return;
    tooltipId = id;
    tooltip.replaceChildren(el("strong", {text: `第 ${entries.indexOf(entry) + 1} 次提问 · ${time(entry.created)}`}),
      el("span", {text: excerpt(entry.summary)}));
    tooltip.hidden = false;
    const rect = control.dot.getBoundingClientRect();
    const area = frame.getBoundingClientRect();
    tooltip.style.top = `${Math.max(8, Math.min(rect.top - area.top + rect.height / 2 - tooltip.offsetHeight / 2,
      area.height - tooltip.offsetHeight - 8))}px`;
  }

  function jump(id) {
    const entry = entries.find(item => item.id === id);
    if (!entry?.node.isConnected) return;
    onJump();
    closeMenu(); hideTooltip();
    // Scroll only this conversation, never the outer page or project sidebar.
    scroll.scrollTop = Math.max(0, scroll.scrollTop + entry.node.getBoundingClientRect().top -
      scroll.getBoundingClientRect().top - 24);
    if (flashTimer) clearTimeout(flashTimer);
    flashed?.classList.remove("wsp-message-jump");
    flashed = entry.node;
    flashed.classList.add("wsp-message-jump");
    flashTimer = setTimeout(() => { flashed?.classList.remove("wsp-message-jump"); flashed = null; }, 1200);
    // Keep keyboard focus on a visible target after the compact menu closes.
    entry.node.setAttribute("tabindex", "-1");
    entry.node.focus({preventScroll: true});
    update();
  }

  function update() {
    if (disposed || !entries.length || rail.hidden) { latest.hidden = true; return; }
    const threshold = scroll.getBoundingClientRect().top + 40;
    const entry = entries.findLast(item => item.node.getBoundingClientRect().top <= threshold) || entries[0];
    if (activeId !== entry.id) {
      activeId = entry.id;
      for (const [id, control] of controls) {
        const active = id === activeId;
        for (const button of [control.dot, control.option]) {
          button.classList.toggle("active", active);
          if (active) button.setAttribute("aria-current", "true");
          else button.removeAttribute("aria-current");
        }
      }
      const dot = controls.get(activeId)?.dot;
      if (dot) {
        const rect = dot.getBoundingClientRect(), area = rail.getBoundingClientRect();
        if (rect.top < area.top || rect.bottom > area.bottom)
          rail.scrollTop += rect.top - area.top - (rail.clientHeight - rect.height) / 2;
      }
    }
    latest.hidden = !isPinned() && scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= 80;
    if (tooltipId) showTooltip(tooltipId);
  }

  function scheduleUpdate() {
    if (disposed || frameId != null) return;
    frameId = requestAnimationFrame(() => { frameId = null; update(); });
  }

  function render(context) {
    const nextKey = workspaceConversationKey(context.projectId, context.sessionId);
    if (nextKey !== key) {
      key = nextKey; controls.clear(); activeId = "";
      closeMenu(); hideTooltip(); rail.scrollTop = 0;
    }
    entries = context.tab === "chat" && context.sessionId
      ? workspacePromptEntries(context.messages, content.children) : [];
    const available = entries.length > 0;
    rail.hidden = trigger.hidden = !available;
    frame.classList.toggle("has-prompt-navigation", available);
    if (!available) { activeId = ""; closeMenu(); hideTooltip(); latest.hidden = true; }
    const dots = [], options = [], retained = new Set();
    entries.forEach((entry, index) => {
      retained.add(entry.id);
      let control = controls.get(entry.id);
      if (!control) {
        const dot = el("button", {class: "wsp-prompt-dot", type: "button", onclick: () => jump(entry.id),
          "aria-describedby": "wsp-prompt-tooltip"}, el("span", {"aria-hidden": "true"}));
        dot.addEventListener("pointerenter", () => showTooltip(entry.id));
        dot.addEventListener("pointerleave", hideTooltip);
        dot.addEventListener("focus", () => showTooltip(entry.id));
        dot.addEventListener("blur", hideTooltip);
        const label = el("strong"), summary = el("span");
        const option = el("button", {class: "wsp-prompt-option", type: "button", onclick: () => jump(entry.id)}, label, summary);
        control = {dot, option, label, summary}; controls.set(entry.id, control);
      }
      const label = `第 ${index + 1} 次提问${entry.created ? ` · ${time(entry.created)}` : ""}`;
      control.dot.setAttribute("aria-label", `${label}：${excerpt(entry.summary)}`);
      control.label.textContent = label;
      control.summary.textContent = excerpt(entry.summary);
      dots.push(control.dot); options.push(control.option);
    });
    for (const id of controls.keys()) if (!retained.has(id)) controls.delete(id);
    if (!controls.has(tooltipId)) hideTooltip();
    workspaceSyncChildren(rail, dots);
    workspaceSyncChildren(menu, options);
    trigger.setAttribute("aria-label", `提问目录，共 ${entries.length} 次提问`);
    update();
  }

  const toggleMenu = () => {
    hideTooltip();
    menu.hidden = !menu.hidden;
    trigger.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) {
      const option = controls.get(activeId)?.option || menu.firstElementChild;
      if (option) {
        const rect = option.getBoundingClientRect(), area = menu.getBoundingClientRect();
        menu.scrollTop += rect.top - area.top - (menu.clientHeight - rect.height) / 2;
        option.focus({preventScroll: true});
      }
    }
  };
  const returnToLatest = () => {
    onLatest(); closeMenu(); hideTooltip();
    scroll.scrollTop = scroll.scrollHeight;
    update();
  };
  const outside = event => {
    if (!menu.contains(event.target) && !trigger.contains(event.target)) closeMenu();
  };
  const resize = () => { closeMenu(); hideTooltip(); scheduleUpdate(); };
  const keydown = event => {
    if (event.key === "Escape" && !menu.hidden) { event.preventDefault(); closeMenu(true); }
    if (["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key) &&
        (rail.contains(event.target) || menu.contains(event.target))) {
      const buttons = Array.from((rail.contains(event.target) ? rail : menu).children);
      const index = buttons.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
        Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
      event.preventDefault(); buttons[next]?.focus();
    }
  };
  trigger.addEventListener("click", toggleMenu);
  latest.addEventListener("click", returnToLatest);
  scroll.addEventListener("scroll", scheduleUpdate, {passive: true});
  rail.addEventListener("scroll", hideTooltip, {passive: true});
  document.addEventListener("pointerdown", outside);
  document.addEventListener("keydown", keydown);
  window.addEventListener("resize", resize);
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(scheduleUpdate);
  observer?.observe(scroll); observer?.observe(content);
  return {render, close: () => { closeMenu(); hideTooltip(); }, dispose() {
    disposed = true;
    if (frameId != null) cancelAnimationFrame(frameId);
    if (flashTimer) clearTimeout(flashTimer);
    flashed?.classList.remove("wsp-message-jump");
    observer?.disconnect();
    trigger.removeEventListener("click", toggleMenu);
    latest.removeEventListener("click", returnToLatest);
    scroll.removeEventListener("scroll", scheduleUpdate);
    rail.removeEventListener("scroll", hideTooltip);
    document.removeEventListener("pointerdown", outside);
    document.removeEventListener("keydown", keydown);
    window.removeEventListener("resize", resize);
  }};
}
