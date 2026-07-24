import { app, BrowserWindow, globalShortcut, screen } from "electron";
import { randomBytes } from "node:crypto";
import { createServer, type Server } from "node:net";
import { join, resolve } from "node:path";
import type { BackendRuntimeStatus } from "../shared/contracts";
import {
  broadcastOverlaySettings,
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
import {
  backendLogger,
  initializeLogging,
  logger
} from "./logging";
import { restoreMacApplicationActivation } from "./mac-application-activation";
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
let backendProcess: BackendProcessController | null = null;
let backendInitialization: Promise<BackendRuntimeStatus> | null = null;
let backendRestartTimer: NodeJS.Timeout | null = null;
let backendRestartAttempts = 0;
let appShutdownPromise: Promise<void> | null = null;
let appShutdownComplete = false;
let developmentShutdownServer: Server | null = null;
const backendBaseUrl = process.env.ADVX_BACKEND_URL ?? "http://127.0.0.1:8765";
const localToken = process.env.ADVX_LOCAL_TOKEN ?? randomBytes(32).toString("base64url");
const backendClient = new BackendClient({
  baseUrl: backendBaseUrl,
  localToken
});

function requestApplicationQuitFromSignal(signal: NodeJS.Signals): void {
  logger.info("app.shutdown.signal-requested", { signal });
  app.quit();
}

process.on("SIGINT", () => requestApplicationQuitFromSignal("SIGINT"));
process.on("SIGTERM", () => requestApplicationQuitFromSignal("SIGTERM"));

function startDevelopmentShutdownControl(): Promise<void> {
  const socketPath = process.env.ADVX_DESKTOP_SHUTDOWN_SOCKET;
  if (!socketPath) return Promise.resolve();

  return new Promise((resolveStart, rejectStart) => {
    const server = createServer((socket) => {
      socket.once("error", () => socket.destroy());
      socket.once("data", (data) => {
        if (data.toString("utf8").trim() !== "quit") {
          socket.end("invalid\n");
          return;
        }
        socket.end("ok\n", () => {
          logger.info("app.shutdown.control-requested");
          app.quit();
        });
      });
    });
    const onStartError = (error: Error) => {
      developmentShutdownServer = null;
      rejectStart(error);
    };
    server.once("error", onStartError);
    server.listen(socketPath, () => {
      server.removeListener("error", onStartError);
      server.on("error", (error) => logger.error("app.shutdown.control-failed", { error }));
      developmentShutdownServer = server;
      resolveStart();
    });
  });
}

function stopDevelopmentShutdownControl(): void {
  const server = developmentShutdownServer;
  developmentShutdownServer = null;
  server?.close();
}

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
    logger.info("backend.mode.external", { baseUrl: backendBaseUrl });
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
      baseUrl: backendBaseUrl,
      logger: backendLogger
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
    baseUrl: backendBaseUrl,
    logger: backendLogger
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
    logger.info("backend.initialize.started", { restart });
    if (restart) await backendClient.stop();
    backendClient.beginStartup();
    try {
      if (restart) await controller.restart();
      else await controller.start();
      await backendClient.start();
      backendRestartAttempts = 0;
      logger.info("backend.initialize.completed", { restart });
      return backendClient.currentStatus();
    } catch (error) {
      logger.error("backend.initialize.failed", { error, restart });
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
    logger.error("backend.recovery.exhausted", {
      attempt: backendRestartAttempts,
      ...exit
    });
    backendClient.failStartup(
      new Error(
        `本地后端连续退出，已停止自动恢复（${exit.signal ?? `exit ${exit.code ?? "unknown"}`}）。`
      )
    );
    return;
  }
  backendClient.beginStartup();
  const delayMs = [500, 1_500, 3_000][backendRestartAttempts - 1] ?? 3_000;
  logger.warn("backend.recovery.scheduled", {
    attempt: backendRestartAttempts,
    delayMs,
    ...exit
  });
  backendRestartTimer = setTimeout(() => {
    backendRestartTimer = null;
    void initializeBackend().catch(() => scheduleBackendRecovery(exit));
  }, delayMs);
}

function openControlWindow(): BrowserWindow {
  const window = createControlWindow();
  logger.info("window.control.opened", { webContentsId: window.webContents.id });
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
    logger.info("window.control.closed");
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
  if (hasSingleInstanceLock) logger.info("app.shutdown.started");
  quitRequested = true;
  stopDevelopmentShutdownControl();
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
  logger.info("app.initialize.started");
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
    logger.warn("backend.process.unexpected-exit", exit);
    void backendClient.stop().finally(() => scheduleBackendRecovery(exit));
  });
  registerDesktopIpc(
    () => controlWindow,
    confirmControlWindowClose,
    backendClient,
    () => initializeBackend(true)
  );
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
    logger.warn("action.emergency-stop");
    hideOverlay();
    controlWindow?.webContents.send("session:emergency-stop");
    controlWindow?.show();
    controlWindow?.focus();
  });
  if (!emergencyShortcutRegistered) {
    console.error("Failed to register the Overlay emergency shortcut");
  }
  logger.info("app.initialize.completed");

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
  initializeLogging();
  app.on("second-instance", () => {
    logger.info("app.second-instance.focus-requested");
    showControlWindow();
  });
  void app
    .whenReady()
    .then(async () => {
      restoreMacApplicationActivation();
      try {
        await startDevelopmentShutdownControl();
      } catch (error) {
        logger.warn("app.shutdown.control-unavailable", { error });
      }
      await initializeApplication();
    })
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
    if (hasSingleInstanceLock) logger.info("app.shutdown.completed");
    appShutdownComplete = true;
    allowControlWindowClose = true;
    if (controlWindow && !controlWindow.isDestroyed()) controlWindow.destroy();
    applicationTray?.tray.destroy();
    applicationTray = null;
    setTraySmokeHandle(null);
    app.quit();
  });
});
