"use strict";

/* Keep native command and file reference text behind inline mentions. */
function createWorkspaceComposer(input, createMention, createFileMention) {
  function readText(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.data.replaceAll("\u00a0", " ");
    if (node.nodeType === Node.ELEMENT_NODE && node.dataset.mentionText) return node.dataset.mentionText;
    if (node.nodeName === "BR") return "\n";
    let text = "";
    for (const child of node.childNodes) {
      const block = child.nodeName === "DIV" || child.nodeName === "P";
      if (block && text && !text.endsWith("\n")) text += "\n";
      text += readText(child);
      if (block && child.nextSibling && !text.endsWith("\n")) text += "\n";
    }
    return text;
  }

  // Editable browsers keep a final line break as a caret placeholder.
  function value() {
    const text = readText(input);
    return text.endsWith("\n") ? text.slice(0, -1) : text;
  }

  function textNode(text) {
    return document.createTextNode(text.endsWith("\n") ? text + "\n" : text);
  }

  function offsetAt(node, offset) {
    const range = document.createRange();
    range.selectNodeContents(input);
    range.setEnd(node, offset);
    return readText(range.cloneContents()).length;
  }

  function selectionOffsets() {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !input.contains(selection.anchorNode) || !input.contains(selection.focusNode)) return null;
    return {
      anchor: offsetAt(selection.anchorNode, selection.anchorOffset),
      focus: offsetAt(selection.focusNode, selection.focusOffset),
    };
  }

  function pointAt(offset) {
    const walker = document.createTreeWalker(input, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (node.parentElement?.closest("[data-mention-text]")) return NodeFilter.FILTER_REJECT;
        return node.nodeType === Node.TEXT_NODE || node.dataset.mentionText || node.nodeName === "BR"
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      const index = Array.prototype.indexOf.call(node.parentNode.childNodes, node);
      const start = offsetAt(node.parentNode, index);
      const length = readText(node).length;
      if (offset > start + length) continue;
      if (node.nodeType === Node.TEXT_NODE) return [node, Math.max(0, offset - start)];
      return [node.parentNode, index + (offset > start ? 1 : 0)];
    }
    return [input, input.childNodes.length];
  }

  function setSelectionRange(anchor, focus = anchor) {
    const selection = window.getSelection();
    if (!selection) return;
    const start = pointAt(anchor), end = pointAt(focus);
    selection.setBaseAndExtent(start[0], start[1], end[0], end[1]);
  }

  function highlightSkill(skill) {
    const chip = input.querySelector("[data-skill-command]");
    if (chip?.dataset.skillCommand === (skill ? `/${skill.name}` : undefined)) return;
    const selection = selectionOffsets();
    if (chip) chip.replaceWith(document.createTextNode(chip.dataset.mentionText));
    const text = value();
    const prefix = skill ? text.match(/^\s*\/[^\s]+/)?.[0] : "";
    if (prefix) {
      const mention = createMention(skill);
      prepareMention(mention, `/${skill.name}`, `技能 ${skill.name}`);
      mention.dataset.skillCommand = `/${skill.name}`;
      const range = document.createRange();
      const start = pointAt(prefix.indexOf("/")), end = pointAt(prefix.length);
      range.setStart(start[0], start[1]);
      range.setEnd(end[0], end[1]);
      range.deleteContents();
      range.insertNode(mention);
    }
    if (selection) setSelectionRange(selection.anchor, selection.focus);
  }

  function prepareMention(mention, text, label) {
    mention.contentEditable = "false";
    mention.dataset.mentionText = text;
    mention.setAttribute("aria-label", label);
    mention.setAttribute("draggable", "false");
  }

  function insertFileReference(path, start, end, insertSeparator = true) {
    const mention = createFileMention(path);
    prepareMention(mention, `@${path}`, `引用文件 ${path}`);
    mention.dataset.filePath = path;
    const space = !insertSeparator || /^\s/.test(value().slice(end)) ? "" : " ";
    input.focus();
    setSelectionRange(start, end);
    // Native editing keeps inserting, deleting and restoring references undoable.
    document.execCommand("insertHTML", false, mention.outerHTML + space);
    setSelectionRange(start + path.length + 1 + space.length);
  }

  function isFileReferenceAt(offset) {
    return Array.from(input.querySelectorAll("[data-file-path]")).some((mention) => {
      const index = Array.prototype.indexOf.call(mention.parentNode.childNodes, mention);
      const start = offsetAt(mention.parentNode, index);
      return offset > start && offset <= start + mention.dataset.mentionText.length;
    });
  }

  function clearFileReferences() {
    for (const mention of input.querySelectorAll("[data-file-path]")) {
      mention.replaceWith(document.createTextNode(mention.dataset.mentionText));
    }
  }

  function deleteMentionBackward() {
    const selection = window.getSelection();
    const offsets = selectionOffsets();
    if (!selection?.isCollapsed || !offsets) return false;
    const caret = offsets.anchor;
    const text = value();
    for (const mention of Array.from(input.querySelectorAll("[data-mention-text]")).reverse()) {
      const index = Array.prototype.indexOf.call(mention.parentNode.childNodes, mention);
      const start = offsetAt(mention.parentNode, index);
      const end = start + mention.dataset.mentionText.length;
      // Include the single separator inserted after picking a reference.
      if (caret !== end && !(caret === end + 1 && text.slice(end, caret) === " ")) continue;
      const range = document.createRange();
      range.selectNode(mention);
      const point = pointAt(caret);
      range.setEnd(point[0], point[1]);
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("delete");
      return !input.contains(mention);
    }
    return false;
  }

  // Copy native slash syntax so pasted mentions can be recognized again.
  function copySelection(event) {
    const selection = selectionOffsets();
    if (!event.clipboardData || !selection || selection.anchor === selection.focus) return false;
    event.preventDefault();
    event.clipboardData.setData("text/plain", value().slice(
      Math.min(selection.anchor, selection.focus), Math.max(selection.anchor, selection.focus)));
    return true;
  }
  input.addEventListener("copy", (event) => {
    copySelection(event);
  });
  input.addEventListener("cut", (event) => {
    if (copySelection(event)) document.execCommand("delete");
  });

  return {
    get value() { return value(); },
    set value(text) { input.replaceChildren(...(text ? [textNode(text)] : [])); },
    snapshot() {
      return { text: value(), references: Array.from(input.querySelectorAll("[data-file-path]"), mention => ({
        path: mention.dataset.filePath,
        start: offsetAt(mention.parentNode, Array.prototype.indexOf.call(mention.parentNode.childNodes, mention)),
      })) };
    },
    restore(draft) {
      const text = typeof draft?.text === "string" ? draft.text : "";
      input.replaceChildren(...(text ? [textNode(text)] : []));
      for (const reference of [...(draft?.references || [])].sort((a, b) => b.start - a.start)) {
        const { path, start } = reference;
        if (typeof path !== "string" || !Number.isInteger(start) || start < 0 ||
            text.slice(start, start + path.length + 1) !== `@${path}`) continue;
        const mention = createFileMention(path);
        prepareMention(mention, `@${path}`, `引用文件 ${path}`);
        mention.dataset.filePath = path;
        const range = document.createRange();
        const begin = pointAt(start), end = pointAt(start + path.length + 1);
        range.setStart(...begin); range.setEnd(...end);
        range.deleteContents(); range.insertNode(mention);
      }
    },
    get selectionStart() {
      const selection = selectionOffsets();
      return selection ? Math.min(selection.anchor, selection.focus) : readText(input).length;
    },
    setSelectionRange,
    highlightSkill,
    insertFileReference,
    isFileReferenceAt,
    clearFileReferences,
    deleteMentionBackward,
    get fileReferences() {
      return [...new Set(Array.from(input.querySelectorAll("[data-file-path]"), mention => mention.dataset.filePath))]
        .map(path => ({ path }));
    },
  };
}
