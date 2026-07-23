import { app, BrowserWindow, globalShortcut } from "electron";
import { configureDisplayCapture, registerDesktopIpc } from "./ipc/register-desktop-ipc";
import { createControlWindow } from "./windows/control";
import { hideOverlay } from "./windows/overlay";

let controlWindow: BrowserWindow | null = null;

app.whenReady().then(() => {
  configureDisplayCapture();
  registerDesktopIpc(() => controlWindow);
  controlWindow = createControlWindow();

  globalShortcut.register("CommandOrControl+Shift+X", () => {
    hideOverlay();
    controlWindow?.webContents.send("session:emergency-stop");
    controlWindow?.show();
    controlWindow?.focus();
  });

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
  globalShortcut.unregisterAll();
  controlWindow = null;
});
