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
let overlaySettingsReady = false;
let displaySyncPending = false;

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
  registerDesktopIpc(() => controlWindow);
  controlWindow = createControlWindow();
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
      controlWindow = createControlWindow();
      broadcastOverlaySettings(() => controlWindow, getOverlaySettings());
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  globalShortcut.unregisterAll();
  screen.removeListener("display-added", syncOverlayToDisplays);
  screen.removeListener("display-removed", syncOverlayToDisplays);
  screen.removeListener("display-metrics-changed", syncOverlayToDisplays);
  overlaySettingsReady = false;
  displaySyncPending = false;
  controlWindow = null;
});
