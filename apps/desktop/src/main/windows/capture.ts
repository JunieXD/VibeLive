import { BrowserWindow } from "electron";
import { join } from "node:path";
import { loadRenderer } from "./load-renderer";

export function createCaptureWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1,
    height: 1,
    show: false,
    webPreferences: {
      preload: join(__dirname, "../preload/capture.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });

  loadRenderer(window, "capture");
  return window;
}
