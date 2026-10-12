"use strict";

let workspaceMonacoPromise;
const workspaceMonacoBase = new URL("vendor/monaco/", document.currentScript.src);

function loadWorkspaceMonaco() {
  if (!workspaceMonacoPromise) {
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = new URL("monaco.css", workspaceMonacoBase).href;
    document.head.append(style);
    self.MonacoEnvironment = {
      getWorker(_moduleId, label) {
        const language = label === "typescript" || label === "javascript" ? "typescript" :
          label === "json" ? "json" : ["html", "handlebars", "razor"].includes(label) ? "html" :
          ["css", "scss", "less"].includes(label) ? "css" : "editor";
        return new Worker(new URL(language + ".worker.js", workspaceMonacoBase), { type: "module", name: "sona-" + language });
      },
    };
    workspaceMonacoPromise = import(new URL("monaco.js", workspaceMonacoBase).href).then(monaco => {
      monaco.jsonDefaults.setDiagnosticsOptions({ validate: true, enableSchemaRequest: false });
      for (const defaults of [monaco.typescript.typescriptDefaults, monaco.typescript.javascriptDefaults]) {
        defaults.setModeConfiguration({...defaults.modeConfiguration, definitions: false, references: false});
      }
      return monaco;
    }).catch(error => { workspaceMonacoPromise = null; style.remove(); throw error; });
  }
  return workspaceMonacoPromise;
}

function workspaceCodeLanguage(monaco, path) {
  const name = path.split("/").at(-1).toLowerCase();
  const explicit = { "dockerfile": "dockerfile", "makefile": "makefile", ".gitignore": "plaintext", ".editorconfig": "ini",
    ".env": "ini", "cmakelists.txt": "cmake" };
  if (explicit[name]) return monaco.languages.getLanguages().some(item => item.id === explicit[name]) ? explicit[name] : "plaintext";
  const language = monaco.languages.getLanguages().find(item =>
    item.filenames?.some(file => file.toLowerCase() === name) ||
    item.extensions?.some(extension => name.endsWith(extension.toLowerCase())));
  return language?.id || "plaintext";
}
