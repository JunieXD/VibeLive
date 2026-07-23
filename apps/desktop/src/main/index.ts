import { app, BrowserWindow, globalShortcut } from "electron";
import { configureMediaAccess, registerDesktopIpc } from "./ipc/register-desktop-ipc";
import { createControlWindow } from "./windows/control";
import { hideOverlay } from "./windows/overlay";

let controlWindow: BrowserWindow | null = null;
let allowControlWindowClose = false;
let controlWindowCloseRequested = false;
let controlWindowCloseFallback: NodeJS.Timeout | null = null;
let quitRequested = false;

function openControlWindow(): BrowserWindow {
  const window = createControlWindow();
  allowControlWindowClose = false;
  controlWindowCloseRequested = false;
  window.on("close", (event) => {
    if (allowControlWindowClose || window.webContents.isLoadingMainFrame()) return;
    event.preventDefault();
    if (controlWindowCloseRequested) return;
    controlWindowCloseRequested = true;
    window.webContents.send("app:request-close");
    controlWindowCloseFallback = setTimeout(() => {
      allowControlWindowClose = true;
      window.destroy();
    }, 5_000);
  });
  window.on("closed", () => {
    if (controlWindowCloseFallback) clearTimeout(controlWindowCloseFallback);
    controlWindowCloseFallback = null;
    if (controlWindow === window) controlWindow = null;
  });
  return window;
}

function confirmControlWindowClose(): void {
  const window = controlWindow;
  if (!window || window.isDestroyed()) return;
  if (controlWindowCloseFallback) clearTimeout(controlWindowCloseFallback);
  controlWindowCloseFallback = null;
  allowControlWindowClose = true;
  window.close();
  if (quitRequested) setImmediate(() => app.quit());
}

app.whenReady().then(() => {
  configureMediaAccess(() => controlWindow);
  registerDesktopIpc(() => controlWindow, confirmControlWindowClose);
  controlWindow = openControlWindow();

  globalShortcut.register("CommandOrControl+Shift+X", () => {
    hideOverlay();
    controlWindow?.webContents.send("session:emergency-stop");
    controlWindow?.show();
    controlWindow?.focus();
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      controlWindow = openControlWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  quitRequested = true;
  globalShortcut.unregisterAll();
});
