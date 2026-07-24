import { app, BrowserWindow, globalShortcut, screen } from "electron";
import { randomBytes } from "node:crypto";
import { join, resolve } from "node:path";
import type { BackendRuntimeStatus } from "../shared/contracts";
import {
  broadcastOverlaySettings,
  configureSavedModelConfig,
  configureMediaAccess,
  registerDesktopIpc
} from "./ipc/register-desktop-ipc";
import { BackendClient } from "./backend/backend-client";
import { loadRuntimeSessionId } from "./backend/runtime-session-state";
import {
  ExternalBackendProcess,
  SpawnedBackendProcess,
  type BackendProcessController,
  type BackendProcessExit
} from "./backend/backend-process";
import {
  createApplicationTray,
  TRAY_MENU_ITEM_IDS,
  type ApplicationTray
} from "./application-tray";
import {
  getOverlaySettings,
  initializeOverlaySettings,
  reconcileOverlayTarget
} from "./overlay-settings";
import { createControlWindow } from "./windows/control";
import { hideOverlay } from "./windows/overlay";

let controlWindow: BrowserWindow | null = null;
let applicationTray: ApplicationTray | null = null;
let allowControlWindowClose = false;
let controlWindowCloseRequested = false;
let controlWindowCloseFallback: NodeJS.Timeout | null = null;
let quitRequested = false;
let overlaySettingsReady = false;
let displaySyncPending = false;
let savedProviderConfigChecked = false;
let backendProcess: BackendProcessController | null = null;
let backendInitialization: Promise<BackendRuntimeStatus> | null = null;
let backendRestartTimer: NodeJS.Timeout | null = null;
let backendRestartAttempts = 0;
let appShutdownPromise: Promise<void> | null = null;
let appShutdownComplete = false;
const backendBaseUrl = process.env.ADVX_BACKEND_URL ?? "http://127.0.0.1:8765";
const localToken = process.env.ADVX_LOCAL_TOKEN ?? randomBytes(32).toString("base64url");
const backendClient = new BackendClient({
  baseUrl: backendBaseUrl,
  localToken
});

type TraySmokeHandle = ApplicationTray & {
  quitMenuItemId: typeof TRAY_MENU_ITEM_IDS.quit;
};

function setTraySmokeHandle(handle: TraySmokeHandle | null): void {
  if (process.env.ADVX_TRAY_SMOKE !== "1") return;
  const testGlobal = globalThis as typeof globalThis & {
    __advxTraySmoke?: TraySmokeHandle;
  };
  if (handle) testGlobal.__advxTraySmoke = handle;
  else delete testGlobal.__advxTraySmoke;
}

function createBackendProcessController(): BackendProcessController {
  const externalOverride = process.env.ADVX_BACKEND_EXTERNAL;
  const externallyManaged =
    externalOverride === "1" ||
    (externalOverride !== "0" && process.env.ADVX_BACKEND_URL !== undefined);
  if (externallyManaged) {
    return new ExternalBackendProcess({ baseUrl: backendBaseUrl });
  }

  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    ADVX_BACKEND_URL: backendBaseUrl,
    ADVX_LOCAL_TOKEN: localToken,
    ADVX_DATA_DIR: app.isPackaged
      ? join(app.getPath("userData"), "data")
      : resolve(app.getAppPath(), "../..", ".advx-data")
  };
  if (app.isPackaged) {
    return new SpawnedBackendProcess({
      command:
        process.env.ADVX_BACKEND_EXECUTABLE ??
        join(
          process.resourcesPath,
          "backend",
          process.platform === "win32" ? "advx-backend.exe" : "advx-backend"
        ),
      cwd: process.resourcesPath,
      env: environment,
      baseUrl: backendBaseUrl
    });
  }

  const repositoryRoot = resolve(app.getAppPath(), "../..");
  const backendPort = new URL(backendBaseUrl).port || "8765";
  return new SpawnedBackendProcess({
    command: "uv",
    args: [
      "run",
      "--project",
      "apps/backend",
      "uvicorn",
      "advx_backend.main:app",
      "--app-dir",
      "apps/backend/src",
      "--host",
      "127.0.0.1",
      "--port",
      backendPort
    ],
    cwd: repositoryRoot,
    env: environment,
    baseUrl: backendBaseUrl
  });
}

async function initializeBackend(restart = false): Promise<BackendRuntimeStatus> {
  if (backendInitialization) return backendInitialization;
  const controller = backendProcess;
  if (!controller) throw new Error("本地后端控制器尚未初始化。");
  if (restart && backendRestartTimer) {
    clearTimeout(backendRestartTimer);
    backendRestartTimer = null;
  }

  backendInitialization = (async () => {
    if (restart) await backendClient.stop();
    backendClient.beginStartup();
    try {
      if (restart) await controller.restart();
      else await controller.start();
      await backendClient.start();
      backendRestartAttempts = 0;
      return backendClient.currentStatus();
    } catch (error) {
      backendClient.failStartup(error);
      throw error;
    }
  })().finally(() => {
    backendInitialization = null;
  });
  return backendInitialization;
}

function scheduleBackendRecovery(exit: BackendProcessExit): void {
  if (quitRequested || backendRestartTimer) return;
  backendRestartAttempts += 1;
  if (backendRestartAttempts > 3) {
    backendClient.failStartup(
      new Error(
        `本地后端连续退出，已停止自动恢复（${exit.signal ?? `exit ${exit.code ?? "unknown"}`}）。`
      )
    );
    return;
  }
  backendClient.beginStartup();
  const delayMs = [500, 1_500, 3_000][backendRestartAttempts - 1] ?? 3_000;
  backendRestartTimer = setTimeout(() => {
    backendRestartTimer = null;
    void initializeBackend().catch(() => scheduleBackendRecovery(exit));
  }, delayMs);
}

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
      app.quit();
    }, 5_000);
  });
  window.on("closed", () => {
    if (controlWindowCloseFallback) clearTimeout(controlWindowCloseFallback);
    controlWindowCloseFallback = null;
    if (controlWindow === window) controlWindow = null;
    if (quitRequested && !appShutdownPromise) setImmediate(() => app.quit());
  });
  return window;
}

function showControlWindow(): void {
  if (quitRequested || appShutdownPromise) return;
  const window = controlWindow;
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

function confirmControlWindowClose(): void {
  const window = controlWindow;
  if (!window || window.isDestroyed()) return;
  if (controlWindowCloseFallback) clearTimeout(controlWindowCloseFallback);
  controlWindowCloseFallback = null;
  allowControlWindowClose = true;
  window.close();
  setImmediate(() => app.quit());
}

function prepareApplicationShutdown(): void {
  quitRequested = true;
  globalShortcut.unregisterAll();
  screen.removeListener("display-added", syncOverlayToDisplays);
  screen.removeListener("display-removed", syncOverlayToDisplays);
  screen.removeListener("display-metrics-changed", syncOverlayToDisplays);
  overlaySettingsReady = false;
  displaySyncPending = false;
  if (backendRestartTimer) clearTimeout(backendRestartTimer);
  backendRestartTimer = null;
}

async function stopApplicationResources(): Promise<void> {
  await backendClient.stop().catch((error: unknown) =>
    console.error("Failed to stop the backend client", error)
  );
  await backendProcess?.stop().catch((error: unknown) =>
    console.error("Failed to stop the backend process", error)
  );
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

async function initializeApplication(): Promise<void> {
  screen.on("display-added", syncOverlayToDisplays);
  screen.on("display-removed", syncOverlayToDisplays);
  screen.on("display-metrics-changed", syncOverlayToDisplays);

  await initializeOverlaySettings();
  overlaySettingsReady = true;
  await reconcileOverlayTarget();

  configureMediaAccess(() => controlWindow);
  const recoverableRuntimeSessionId = await loadRuntimeSessionId(app.getPath("userData"));
  if (recoverableRuntimeSessionId) {
    backendClient.restoreRecoverableRuntimeSession(recoverableRuntimeSessionId);
  }
  backendProcess = createBackendProcessController();
  backendProcess.onUnexpectedExit((exit) => {
    void backendClient.stop().finally(() => scheduleBackendRecovery(exit));
  });
  registerDesktopIpc(
    () => controlWindow,
    confirmControlWindowClose,
    backendClient,
    () => initializeBackend(true)
  );
  backendClient.onStatus((status) => {
    if (status.connection === "disconnected") {
      savedProviderConfigChecked = false;
      return;
    }
    if (status.connection !== "connected" || savedProviderConfigChecked) return;
    savedProviderConfigChecked = true;
    if (recoverableRuntimeSessionId) return;
    void configureSavedModelConfig(backendClient).catch((error: unknown) =>
      console.error("Failed to restore saved provider configuration", error)
    );
  });
  controlWindow = openControlWindow();
  const trayIcon = await app.getFileIcon(process.execPath, { size: "small" });
  if (trayIcon.isEmpty()) throw new Error("Windows system tray icon is empty.");
  applicationTray = createApplicationTray({
    icon: trayIcon,
    showControlWindow,
    quitApplication: () => app.quit()
  });
  setTraySmokeHandle({
    ...applicationTray,
    quitMenuItemId: TRAY_MENU_ITEM_IDS.quit
  });
  broadcastOverlaySettings(() => controlWindow, getOverlaySettings());
  void initializeBackend().catch((error: unknown) =>
    console.error("Failed to initialize backend", error)
  );

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
    if (quitRequested || appShutdownPromise) return;
    if (BrowserWindow.getAllWindows().length === 0) {
      controlWindow = openControlWindow();
      broadcastOverlaySettings(() => controlWindow, getOverlaySettings());
    }
    showControlWindow();
  });
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  quitRequested = true;
  app.quit();
} else {
  app.on("second-instance", showControlWindow);
  void app
    .whenReady()
    .then(initializeApplication)
    .catch((error: unknown) => {
      console.error("Failed to initialize ADVX Live", error);
      app.quit();
    });
}

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  quitRequested = true;
  if (appShutdownComplete) return;
  event.preventDefault();
  if (appShutdownPromise) return;
  if (controlWindow && !controlWindow.isDestroyed() && !allowControlWindowClose) {
    controlWindow.close();
    return;
  }

  prepareApplicationShutdown();
  appShutdownPromise = stopApplicationResources().finally(() => {
    appShutdownComplete = true;
    allowControlWindowClose = true;
    if (controlWindow && !controlWindow.isDestroyed()) controlWindow.destroy();
    applicationTray?.tray.destroy();
    applicationTray = null;
    setTraySmokeHandle(null);
    app.quit();
  });
});
