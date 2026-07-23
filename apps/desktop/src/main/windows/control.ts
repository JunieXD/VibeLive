import { BrowserWindow } from "electron";
import { join } from "node:path";
import { loadRenderer } from "./load-renderer";

export function createControlWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1040,
    height: 720,
    minWidth: 760,
    minHeight: 560,
    show: false,
    title: "ADVX Live",
    webPreferences: {
      preload: join(__dirname, "../preload/control.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });

  window.once("ready-to-show", () => window.show());
  loadRenderer(window, "control");
  return window;
}
