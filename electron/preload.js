"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// Match the small Tauri surface used by the existing browser UI. The main
// process checks the sender origin and exposes only the listed operations.
contextBridge.exposeInMainWorld("__TAURI__", {
  core: {
    invoke(command, args = {}) {
      if (command === "plugin:dialog|open") return ipcRenderer.invoke("sona:choose-directory", args.options);
      if (command === "open_website_login") return ipcRenderer.invoke("sona:open-website-login", args.url);
      if (command === "get_autostart") return ipcRenderer.invoke("sona:get-autostart");
      if (command === "set_autostart") return ipcRenderer.invoke("sona:set-autostart", args.enabled);
      if (command === "request_task_attention") return ipcRenderer.invoke("sona:request-task-attention");
      return Promise.reject(new Error(`Unsupported desktop command: ${command}`));
    },
  },
  dialog: {
    open(options) { return ipcRenderer.invoke("sona:choose-directory", options); },
  },
  notification: {
    isPermissionGranted() { return Promise.resolve(true); },
    requestPermission() { return Promise.resolve("granted"); },
    sendNotification(options) { return ipcRenderer.invoke("sona:notify-answer-complete", options.body); },
  },
});
