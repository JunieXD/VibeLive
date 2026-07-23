import {
  app,
  BrowserWindow,
  desktopCapturer,
  ipcMain,
  safeStorage,
  session,
  systemPreferences
} from "electron";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type {
  BarrageEvent,
  DesktopSource,
  MediaAccessSnapshot,
  MediaAccessStatus,
  ModelConfig,
  SaveModelConfigResult
} from "../../shared/contracts";
import {
  clearOverlay,
  hideOverlay,
  pushBarrage,
  showOverlay
} from "../windows/overlay";

let selectedSourceId: string | null = null;
let displayCaptureAuthorization: { webContentsId: number; expiresAt: number } | null = null;

function hasDisplayCaptureAuthorization(webContentsId: number): boolean {
  return (
    displayCaptureAuthorization?.webContentsId === webContentsId &&
    displayCaptureAuthorization.expiresAt >= Date.now()
  );
}

async function listDesktopSources(controlWindow: BrowserWindow | null): Promise<DesktopSource[]> {
  const sources = await desktopCapturer.getSources({
    types: ["screen", "window"],
    thumbnailSize: { width: 480, height: 270 },
    fetchWindowIcons: true
  });
  const internalSourceIds = new Set(
    BrowserWindow.getAllWindows().map((window) => window.getMediaSourceId())
  );
  if (controlWindow) internalSourceIds.add(controlWindow.getMediaSourceId());

  return sources
    .filter((source) => !internalSourceIds.has(source.id))
    .map((source) => ({
      id: source.id,
      name: source.name,
      thumbnailUrl: source.thumbnail.toDataURL(),
      appIconUrl: source.appIcon?.isEmpty() === false ? source.appIcon.toDataURL() : null,
      kind: source.id.startsWith("screen:") ? "screen" : "window"
    }));
}

async function saveModelConfig(config: ModelConfig): Promise<SaveModelConfigResult> {
  const configDirectory = app.getPath("userData");
  await mkdir(configDirectory, { recursive: true });

  const storedConfig: Record<string, string> = {
    baseUrl: config.baseUrl.trim(),
    model: config.model.trim()
  };

  let securelyStored = false;
  if (config.apiKey && safeStorage.isEncryptionAvailable()) {
    storedConfig.encryptedApiKey = safeStorage.encryptString(config.apiKey).toString("base64");
    securelyStored = true;
  }

  await writeFile(
    join(configDirectory, "model-config.json"),
    JSON.stringify(storedConfig, null, 2),
    "utf8"
  );
  return { ok: true, securelyStored };
}

function getMediaAccessStatus(): MediaAccessSnapshot {
  return {
    microphone: systemPreferences.getMediaAccessStatus("microphone"),
    screen: systemPreferences.getMediaAccessStatus("screen")
  };
}

async function requestMicrophonePermission(): Promise<MediaAccessStatus> {
  if (process.platform === "darwin") {
    await systemPreferences.askForMediaAccess("microphone");
  }
  return systemPreferences.getMediaAccessStatus("microphone");
}

export function configureMediaAccess(getControlWindow: () => BrowserWindow | null): void {
  const isControlWebContents = (webContents: Electron.WebContents | null): boolean =>
    webContents !== null && webContents.id === getControlWindow()?.webContents.id;

  session.defaultSession.setPermissionCheckHandler((webContents, permission, _origin, details) => {
    if (!isControlWebContents(webContents) || !details.isMainFrame) return false;
    const permissionName: string = permission;
    return (
      permissionName === "display-capture" ||
      (permission === "media" && details.mediaType === "audio")
    );
  });

  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      if (!isControlWebContents(webContents)) {
        callback(false);
        return;
      }

      if (permission === "display-capture") {
        callback(true);
        return;
      }

      const mediaTypes =
        permission === "media" && "mediaTypes" in details ? details.mediaTypes : undefined;
      const isMainFrame = "isMainFrame" in details && details.isMainFrame;
      callback(
        permission === "media" &&
          isMainFrame &&
          ((mediaTypes?.length === 1 && mediaTypes[0] === "audio") ||
            (mediaTypes?.length === 0 && hasDisplayCaptureAuthorization(webContents.id)))
      );
    }
  );

  session.defaultSession.setDisplayMediaRequestHandler(async (request, callback) => {
    try {
      const controlFrame = getControlWindow()?.webContents.mainFrame;
      if (
        !hasDisplayCaptureAuthorization(getControlWindow()?.webContents.id ?? -1) ||
        !request.videoRequested ||
        request.audioRequested ||
        request.frame?.frameTreeNodeId !== controlFrame?.frameTreeNodeId
      ) {
        displayCaptureAuthorization = null;
        callback({});
        return;
      }

      displayCaptureAuthorization = null;
      const sources = await desktopCapturer.getSources({ types: ["screen", "window"] });
      const source = sources.find((candidate) => candidate.id === selectedSourceId);
      callback(source ? { video: source } : {});
    } catch {
      callback({});
    }
  });
}

export function registerDesktopIpc(getControlWindow: () => BrowserWindow | null): void {
  ipcMain.handle("desktop:list-sources", () => listDesktopSources(getControlWindow()));
  ipcMain.handle("desktop:select-source", async (event, sourceId: string) => {
    if (event.sender.id !== getControlWindow()?.webContents.id) return false;
    const sources = await listDesktopSources(getControlWindow());
    const exists = sources.some((source) => source.id === sourceId);
    selectedSourceId = exists ? sourceId : null;
    displayCaptureAuthorization = exists
      ? { webContentsId: event.sender.id, expiresAt: Date.now() + 60_000 }
      : null;
    return exists;
  });
  ipcMain.handle("media:get-access-status", getMediaAccessStatus);
  ipcMain.handle("media:request-microphone", requestMicrophonePermission);
  ipcMain.handle("overlay:show", showOverlay);
  ipcMain.handle("overlay:hide", hideOverlay);
  ipcMain.handle("overlay:clear", clearOverlay);
  ipcMain.handle("overlay:push", (_event, event: BarrageEvent) => pushBarrage(event));
  ipcMain.handle("config:save-model", (_event, config: ModelConfig) => saveModelConfig(config));
}
