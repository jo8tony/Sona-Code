"use strict";

function createCodeNavigation({monaco, editor, canvas, client, snapshot, openTarget, onError}) {
  const disposables = [], toLsp = p => ({line: p.lineNumber - 1, character: p.column - 1});
  const range = r => ({startLineNumber: r.start.line + 1, startColumn: r.start.character + 1,
    endLineNumber: r.end.line + 1, endColumn: r.end.character + 1});
  let generation = 0, controller = null, cancelMenu = null, disposed = false;
  const labels = {definition: "定义", implementation: "实现", references: "引用"};
  function invalidate() { ++generation; controller?.abort(); controller = null; cancelMenu?.(); cancelMenu = null; }
  function sameSource(a, b) { return a && b && a.projectId === b.projectId && a.path === b.path && a.epoch === b.epoch && a.version === b.version; }
  function candidates(targets, kind) {
    return new Promise(resolve => {
      let index = 0, finished = false;
      const menu = el("div", {class: "wsp-definition-menu", role: "listbox", "aria-label": "选择" + labels[kind], tabindex: "0"});
      const buttons = targets.map((target, i) => el("button", {type: "button", role: "option",
        text: target.path + ":" + (target.range.start.line + 1) + ":" + (target.range.start.character + 1), onclick: () => finish(target)}));
      function update() {
        buttons.forEach((button, i) => { button.setAttribute("aria-selected", String(i === index)); button.tabIndex = i === index ? 0 : -1; });
        buttons[index].focus();
      }
      function finish(target = null) {
        if (finished) return; finished = true; menu.remove(); cancelMenu = null; resolve(target);
      }
      cancelMenu = () => finish();
      menu.append(el("div", {class: "wsp-navigation-title", text: labels[kind] + "位置（" + targets.length + "）"}), ...buttons);
      canvas.append(menu); update();
      menu.addEventListener("keydown", event => {
        if (!["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key) || event.isComposing) return;
        event.preventDefault(); event.stopPropagation?.();
        if (event.key === "Escape") { finish(); editor.focus(); }
        else if (event.key === "Enter") finish(targets[index]);
        else { index = (index + (event.key === "ArrowDown" ? 1 : -1) + targets.length) % targets.length; update(); }
      });
    });
  }
  async function perform(position, kind = "definition") {
    const source = snapshot(), model = editor.getModel();
    if (disposed || !source || !position || !model?.getWordAtPosition(position)) return false;
    invalidate(); const stamp = generation, groupStamp = client.stamp(source), cursor = editor.getPosition();
    const abort = controller = new AbortController();
    const valid = () => !disposed && stamp === generation && sameSource(source, snapshot()) &&
      groupStamp === client.stamp(source) && JSON.stringify(cursor) === JSON.stringify(editor.getPosition());
    try {
      let targets = await client[kind === "auto" ? "definition" : kind](source, toLsp(position), abort.signal);
      if (!valid()) return false;
      if (kind === "auto") {
        // A definition query that points back to this identifier means the
        // user clicked its declaration, not a use elsewhere in the same file.
        const point = toLsp(position);
        const declaration = targets.some(target => target.path === source.path &&
          target.range.start.line === point.line && target.range.end.line === point.line &&
          target.range.start.character <= point.character && point.character < target.range.end.character);
        kind = declaration ? "references" : "definition";
        if (declaration) targets = await client.references(source, point, abort.signal);
      }
      if (!valid()) return false;
      if (!targets.length) { onError("未找到可打开的" + labels[kind]); return false; }
      const target = targets.length === 1 ? targets[0] : await candidates(targets, kind);
      if (!target || !valid()) return false;
      return await openTarget(source.projectId, target.path, range(target.range), {recordHistory: true, navigationGuard: valid});
    } catch (error) {
      if (valid() && !["cancelled", "stale"].includes(error.code) && error.name !== "AbortError") onError(error.message);
      return false;
    } finally { if (controller === abort) controller = null; }
  }
  for (const language of ["java", "python", "javascript", "typescript"]) {
    for (const kind of ["definition", "implementation", "references"]) {
      const register = {definition: "registerDefinitionProvider", implementation: "registerImplementationProvider", references: "registerReferenceProvider"}[kind];
      const provide = {definition: "provideDefinition", implementation: "provideImplementation", references: "provideReferences"}[kind];
      disposables.push(monaco.languages[register](language, {
        async [provide](model, position, contextOrToken, referenceToken) {
          const token = referenceToken || contextOrToken;
          const source = snapshot();
          if (disposed || !source || editor.getModel() !== model || !model.getWordAtPosition(position)) return [];
          const abort = new AbortController(), cancelled = token.onCancellationRequested(() => abort.abort());
          const groupStamp = client.stamp(source);
          if (token.isCancellationRequested) abort.abort();
          try {
            const targets = await client[kind](source, toLsp(position), abort.signal);
            if (disposed || abort.signal.aborted || !sameSource(source, snapshot()) || groupStamp !== client.stamp(source)) return [];
            return targets.map(target => ({uri: monaco.Uri.from({scheme: "inmemory", authority: source.projectId, path: "/" + target.path}),
              range: range(target.range)}));
          } catch (_) { return []; }
          finally { cancelled.dispose(); }
        },
      }));
    }
  }
  disposables.push(editor.addAction({id: "sona-code.goto-definition", label: "转到项目定义", keybindings: [monaco.KeyCode.F12],
    contextMenuGroupId: "navigation", contextMenuOrder: 1.5,
    precondition: "editorTextFocus && !inComposition", run: () => perform(editor.getPosition())}));
  disposables.push(editor.addAction({id: "sona-code.goto-implementation", label: "转到实现",
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Alt | monaco.KeyCode.KeyB],
    contextMenuGroupId: "navigation", contextMenuOrder: 1.6,
    precondition: "editorTextFocus && !inComposition", run: () => perform(editor.getPosition(), "implementation")}));
  disposables.push(editor.addAction({id: "sona-code.find-references", label: "查找项目引用",
    keybindings: [monaco.KeyMod.Shift | monaco.KeyCode.F12],
    contextMenuGroupId: "navigation", contextMenuOrder: 1.7,
    precondition: "editorTextFocus && !inComposition", run: () => perform(editor.getPosition(), "references")}));
  const targetAt = event => {
    if (!event.ctrlKey || event.altKey || event.shiftKey || event.isComposing || event.button !== 0) return null;
    const target = editor.getTargetAtClientPoint(event.clientX, event.clientY);
    return target?.type === monaco.editor.MouseTargetType.CONTENT_TEXT && target.position &&
      editor.getModel()?.getWordAtPosition(target.position) ? target.position : null;
  };
  const mouseDown = event => {
    const position = targetAt(event); if (!position) return;
    event.preventDefault(); event.stopImmediatePropagation();
    editor.setPosition?.(position); void perform(position, "auto");
  };
  const suppressNative = event => { if (targetAt(event)) { event.preventDefault(); event.stopImmediatePropagation(); } };
  canvas.addEventListener("mousedown", mouseDown, true);
  canvas.addEventListener("mouseup", suppressNative, true);
  canvas.addEventListener("click", suppressNative, true);
  disposables.push(editor.onDidChangeCursorPosition(invalidate), editor.onDidChangeModel(invalidate));
  const unsubscribe = client.subscribe((projectId, family) => {
    const source = snapshot();
    if (source && (!projectId || source.projectId === projectId && workspaceLanguageFamily(source.language) === family)) invalidate();
  });
  return {invalidate, dispose() {
    disposed = true; invalidate(); unsubscribe(); disposables.forEach(item => item.dispose());
    canvas.removeEventListener("mousedown", mouseDown, true); canvas.removeEventListener("mouseup", suppressNative, true);
    canvas.removeEventListener("click", suppressNative, true);
  }};
}
