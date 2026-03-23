const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("xianyuSettings", {
  load: () => ipcRenderer.invoke("xianyu-config-load"),
  save: (apiBase) => ipcRenderer.invoke("xianyu-config-save", apiBase),
});
