"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// Match the small Tauri surface used by the existing browser UI. The main
// process checks the sender origin and handles only these two operations.
contextBridge.exposeInMainWorld("__TAURI__", {
  core: {
    invoke(command, args = {}) {
      if (command === "plugin:dialog|open") return ipcRenderer.invoke("sona:choose-directory", args.options);
      if (command === "open_website_login") return ipcRenderer.invoke("sona:open-website-login", args.url);
      return Promise.reject(new Error(`Unsupported desktop command: ${command}`));
    },
  },
  dialog: {
    open(options) { return ipcRenderer.invoke("sona:choose-directory", options); },
  },
});
