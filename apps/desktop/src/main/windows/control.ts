import { BrowserWindow, shell } from "electron";
import { join } from "node:path";
import { loadRenderer } from "./load-renderer";

export function createControlWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1120,
    minHeight: 720,
    show: false,
    title: "ADVX Live",
    backgroundColor: "#0e0f12",
    webPreferences: {
      preload: join(__dirname, "../preload/control.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  loadRenderer(window, "control");
  return window;
}
