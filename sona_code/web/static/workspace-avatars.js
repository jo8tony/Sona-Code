"use strict";

// Original, bundled SVG illustrations keep the same appearance offline and on
// every platform. Store an ID rather than an image URL in the local app profile.
const WORKSPACE_AVATARS = [
  ["fox", "狐狸"], ["cat", "小猫"], ["dog", "小狗"], ["rabbit", "兔子"],
  ["panda", "熊猫"], ["bear", "小熊"], ["koala", "考拉"], ["tiger", "老虎"],
  ["lion", "狮子"], ["penguin", "企鹅"], ["owl", "猫头鹰"], ["frog", "青蛙"],
  ["pig", "小猪"], ["hamster", "仓鼠"], ["deer", "小鹿"], ["raccoon", "浣熊"],
].map(([id, name]) => ({id, name, src: `avatars/${id}.svg`}));
const WORKSPACE_AVATAR_KEY = "sona-code:account-avatar";
let workspaceAvatarSelection = WORKSPACE_AVATARS[0].id;

function createWorkspaceAvatarPicker(trigger, notify) {
  let picker = null;
  try {
    const saved = localStorage.getItem(WORKSPACE_AVATAR_KEY);
    if (WORKSPACE_AVATARS.some(avatar => avatar.id === saved)) workspaceAvatarSelection = saved;
  } catch (_) { /* Retain the in-memory choice when browser storage is blocked. */ }

  function renderAvatar() {
    const avatar = WORKSPACE_AVATARS.find(item => item.id === workspaceAvatarSelection) || WORKSPACE_AVATARS[0];
    trigger.replaceChildren(el("img", {src: avatar.src, alt: "", width: "36", height: "36", draggable: "false"}));
    trigger.title = `更换头像 · 当前：${avatar.name}`;
    trigger.setAttribute("aria-label", trigger.title);
  }

  function closePicker(restoreFocus = false) {
    if (!picker) return;
    const dialog = picker;
    picker = null;
    dialog.close(); dialog.remove();
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus && trigger.isConnected) trigger.focus({preventScroll: true});
  }

  function positionPicker() {
    if (!picker) return;
    const anchor = trigger.getBoundingClientRect();
    const bounds = picker.getBoundingClientRect();
    const margin = 12;
    picker.style.left = `${Math.max(margin, Math.min(anchor.left, window.innerWidth - bounds.width - margin))}px`;
    picker.style.top = `${Math.max(margin, Math.min(anchor.top - bounds.height - 12,
      window.innerHeight - bounds.height - margin))}px`;
  }

  function openPicker() {
    if (picker) { closePicker(true); return; }
    const options = WORKSPACE_AVATARS.map(avatar => el("button", {
      type: "button", class: "wsp-avatar-option", "aria-label": avatar.name,
      "aria-pressed": String(avatar.id === workspaceAvatarSelection),
      onclick: () => {
        workspaceAvatarSelection = avatar.id;
        renderAvatar();
        try { localStorage.setItem(WORKSPACE_AVATAR_KEY, avatar.id); }
        catch (_) { notify?.("头像已更换，但无法保存到本地；下次打开可能恢复默认头像。", "error"); }
        closePicker(true);
      },
    }, el("span", {class: "wsp-avatar-illustration"},
      el("img", {src: avatar.src, alt: "", width: "48", height: "48", draggable: "false"}),
      el("span", {class: "wsp-avatar-check", "aria-hidden": "true"}, workspaceIcon("check"))),
    el("span", {text: avatar.name})));
    const dialog = el("dialog", {class: "wsp-avatar-picker", id: "wsp-avatar-picker",
      "aria-label": "选择头像", "aria-describedby": "wsp-avatar-hint"},
      el("div", {class: "wsp-avatar-picker-head"}, el("strong", {text: "选择头像"}),
        el("button", {type: "button", class: "wsp-avatar-close", "aria-label": "关闭头像选择",
          onclick: () => closePicker(true)}, workspaceIcon("close"))),
      el("p", {id: "wsp-avatar-hint", text: "选一个喜欢的小伙伴"}),
      el("div", {class: "wsp-avatar-grid"}, ...options));
    dialog.addEventListener("click", event => {
      const bounds = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom)) closePicker(true);
    });
    dialog.addEventListener("cancel", event => { event.preventDefault(); closePicker(true); });
    dialog.addEventListener("close", () => { if (picker === dialog) closePicker(true); });
    dialog.addEventListener("keydown", event => {
      const step = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -4, ArrowDown: 4}[event.key];
      const index = options.indexOf(document.activeElement);
      if (step === undefined || index < 0) return;
      event.preventDefault();
      options[(index + step + options.length) % options.length].focus();
    });
    picker = dialog;
    document.body.append(dialog);
    dialog.showModal();
    positionPicker();
    trigger.setAttribute("aria-expanded", "true");
    options[WORKSPACE_AVATARS.findIndex(avatar => avatar.id === workspaceAvatarSelection)].focus();
  }

  renderAvatar();
  trigger.addEventListener("click", openPicker);
  window.addEventListener("resize", positionPicker);
  return {dispose() {
    closePicker();
    trigger.removeEventListener("click", openPicker);
    window.removeEventListener("resize", positionPicker);
  }};
}
