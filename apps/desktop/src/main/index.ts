import { app, BrowserWindow } from "electron";
import { createControlWindow } from "./windows/control";

let controlWindow: BrowserWindow | null = null;

app.whenReady().then(() => {
  controlWindow = createControlWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      controlWindow = createControlWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  controlWindow = null;
});
