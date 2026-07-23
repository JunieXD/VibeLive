import { app, BrowserWindow, globalShortcut, screen } from "electron";
import {
  broadcastOverlaySettings,
  configureMediaAccess,
  registerDesktopIpc
} from "./ipc/register-desktop-ipc";
import {
  getOverlaySettings,
  initializeOverlaySettings,
  reconcileOverlayTarget
} from "./overlay-settings";
import { createControlWindow } from "./windows/control";
import { hideOverlay } from "./windows/overlay";

let controlWindow: BrowserWindow | null = null;
let allowControlWindowClose = false;
let controlWindowCloseRequested = false;
let controlWindowCloseFallback: NodeJS.Timeout | null = null;
let quitRequested = false;
let overlaySettingsReady = false;
let displaySyncPending = false;

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

function syncOverlayToDisplays(): void {
  if (!overlaySettingsReady) {
    displaySyncPending = true;
    return;
  }

  void reconcileOverlayTarget()
    .then((settings) => broadcastOverlaySettings(() => controlWindow, settings))
    .catch((error: unknown) => console.error("Failed to sync overlay display settings", error));
}

app.whenReady().then(async () => {
  screen.on("display-added", syncOverlayToDisplays);
  screen.on("display-removed", syncOverlayToDisplays);
  screen.on("display-metrics-changed", syncOverlayToDisplays);

  await initializeOverlaySettings();
  overlaySettingsReady = true;
  await reconcileOverlayTarget();

  configureMediaAccess(() => controlWindow);
  registerDesktopIpc(() => controlWindow, confirmControlWindowClose);
  controlWindow = openControlWindow();
  broadcastOverlaySettings(() => controlWindow, getOverlaySettings());

  if (displaySyncPending) {
    displaySyncPending = false;
    syncOverlayToDisplays();
  }

  const emergencyShortcutRegistered = globalShortcut.register("CommandOrControl+Shift+X", () => {
    hideOverlay();
    controlWindow?.webContents.send("session:emergency-stop");
    controlWindow?.show();
    controlWindow?.focus();
  });
  if (!emergencyShortcutRegistered) {
    console.error("Failed to register the Overlay emergency shortcut");
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      controlWindow = openControlWindow();
      broadcastOverlaySettings(() => controlWindow, getOverlaySettings());
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  quitRequested = true;
  globalShortcut.unregisterAll();
  screen.removeListener("display-added", syncOverlayToDisplays);
  screen.removeListener("display-removed", syncOverlayToDisplays);
  screen.removeListener("display-metrics-changed", syncOverlayToDisplays);
  overlaySettingsReady = false;
  displaySyncPending = false;
});
