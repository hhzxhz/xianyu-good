/**
 * Mac 客户端入口：加载本地 admin.html，通过 ?api= 将 REST 指向远端/本机服务端（C-S）。
 */
const { app, BrowserWindow, ipcMain, Menu, shell } = require("electron");
const path = require("path");
const fs = require("fs");

const DEFAULT_API = "http://127.0.0.1:28080";

function configPath() {
  return path.join(app.getPath("userData"), "server.json");
}

function readConfig() {
  try {
    const raw = fs.readFileSync(configPath(), "utf8");
    const j = JSON.parse(raw);
    const base = (j.apiBase || DEFAULT_API).trim().replace(/\/$/, "");
    return { apiBase: base || DEFAULT_API };
  } catch (e) {
    return { apiBase: DEFAULT_API };
  }
}

function saveConfig(c) {
  const apiBase = (c.apiBase || DEFAULT_API).trim().replace(/\/$/, "") || DEFAULT_API;
  fs.writeFileSync(configPath(), JSON.stringify({ apiBase }, null, 2), "utf8");
  return { apiBase };
}

/** @type {BrowserWindow | null} */
let mainWindow = null;

function adminHtmlPath() {
  return path.join(__dirname, "static", "admin.html");
}

function loadMainContent(win) {
  const { apiBase } = readConfig();
  const filePath = adminHtmlPath();
  if (!fs.existsSync(filePath)) {
    win.loadURL(
      "data:text/html;charset=utf-8," +
        encodeURIComponent(
          "<h2>缺少静态文件</h2><p>请在 clients/mac-electron 下执行 <code>npm run sync-static</code> 后再启动。</p>"
        )
    );
    return;
  }
  win.loadFile(filePath, { query: { api: apiBase } });
}

function openSettingsWindow() {
  const w = new BrowserWindow({
    width: 520,
    height: 220,
    resizable: false,
    modal: true,
    parent: mainWindow || undefined,
    webPreferences: {
      preload: path.join(__dirname, "settings-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  w.setMenu(null);
  w.loadFile(path.join(__dirname, "settings.html"));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  const menu = Menu.buildFromTemplate([
    {
      label: "鲸吸购",
      submenu: [
        {
          label: "服务端地址…",
          click: () => openSettingsWindow(),
        },
        { type: "separator" },
        { role: "reload", label: "重新加载" },
        { type: "separator" },
        { role: "toggledevtools" },
        { type: "separator" },
        { role: "quit", label: "退出" },
      ],
    },
    {
      label: "帮助",
      submenu: [
        {
          label: "打开 API 文档（默认地址）",
          click: () => {
            const { apiBase } = readConfig();
            shell.openExternal(apiBase + "/docs");
          },
        },
      ],
    },
  ]);
  Menu.setApplicationMenu(menu);
  loadMainContent(mainWindow);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(() => {
  ipcMain.handle("xianyu-config-load", () => readConfig());
  ipcMain.handle("xianyu-config-save", (event, apiBase) => {
    saveConfig({ apiBase: apiBase || DEFAULT_API });
    const sub = BrowserWindow.fromWebContents(event.sender);
    if (sub && sub !== mainWindow) sub.close();
    if (mainWindow) loadMainContent(mainWindow);
    return readConfig();
  });
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
