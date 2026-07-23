import { BrowserWindow, Rectangle } from "electron";
import { join } from "node:path";
import { loadRenderer } from "./load-renderer";

export function createOverlayWindow(bounds: Rectangle): BrowserWindow {
  const window = new BrowserWindow({
    ...bounds,
    transparent: true,
    frame: false,
    focusable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    webPreferences: {
      preload: join(__dirname, "../preload/overlay.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });

  window.setIgnoreMouseEvents(true);
  loadRenderer(window, "overlay");
  return window;
}
