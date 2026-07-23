import {
  app,
  BrowserWindow,
  desktopCapturer,
  ipcMain,
  safeStorage,
  session,
  systemPreferences
} from "electron";
import { createHash } from "node:crypto";
import { appendFile, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import {
  parseAudienceWorkspaceState,
  serializePersonaMarkdown,
  type AudienceWorkspaceState,
  type Persona
} from "../../shared/audience";
import type {
  BarrageEvent,
  DesktopSource,
  MediaAccessSnapshot,
  MediaAccessStatus,
  ModelConfig,
  SaveAudienceWorkspaceResult,
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
let cameraCaptureAuthorization: { webContentsId: number; expiresAt: number } | null = null;

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

function hasCameraCaptureAuthorization(webContentsId: number): boolean {
  return (
    cameraCaptureAuthorization?.webContentsId === webContentsId &&
    cameraCaptureAuthorization.expiresAt >= Date.now()
  );
}

function consumeCameraCaptureAuthorization(webContentsId: number): boolean {
  const authorized = hasCameraCaptureAuthorization(webContentsId);
  cameraCaptureAuthorization = null;
  return authorized;
}

function audienceWorkspacePath(): string {
  return join(app.getPath("userData"), "audience-workspace.json");
}

function audienceWorkspaceBackupPath(): string {
  return `${audienceWorkspacePath()}.bak`;
}

async function preserveRejectedAudienceWorkspace(raw: string): Promise<string | null> {
  const fingerprint = createHash("sha256").update(raw).digest("hex").slice(0, 12);
  const rejectedPath = join(
    app.getPath("userData"),
    `audience-workspace.rejected-${fingerprint}.json`
  );
  try {
    await writeFile(rejectedPath, raw, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") return null;
  }
  return rejectedPath;
}

async function rejectedWorkspaceError(raw: string, reason: string): Promise<Error> {
  const recoveryPath = await preserveRejectedAudienceWorkspace(raw);
  return new Error(
    recoveryPath
      ? `${reason}。原内容已保留，并复制到 ${recoveryPath}`
      : `${reason}。原配置文件未被覆盖`
  );
}

async function loadAudienceWorkspace(): Promise<AudienceWorkspaceState | null> {
  let raw: string;
  try {
    raw = await readFile(audienceWorkspacePath(), "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    try {
      raw = await readFile(audienceWorkspaceBackupPath(), "utf8");
    } catch (backupError) {
      if ((backupError as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw backupError;
    }
  }

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw await rejectedWorkspaceError(raw, "观众配置文件不是有效 JSON");
  }

  const parsed = parseAudienceWorkspaceState(value);
  if (!parsed.ok) {
    throw await rejectedWorkspaceError(
      raw,
      `观众配置校验失败：${parsed.issues.slice(0, 3).join("；")}`
    );
  }
  return parsed.workspace;
}

function mergePersonaForMode(
  workspace: AudienceWorkspaceState,
  modeId: string,
  personaId: string
): Persona {
  const base = workspace.personas.find((persona) => persona.id === personaId);
  const mode = workspace.modeState.modes.find((candidate) => candidate.id === modeId);
  if (!base || !mode) throw new Error(`无法生成模式 ${modeId} 的人格 ${personaId}`);
  const override = mode.personaOverrides[personaId];
  return {
    ...base,
    ...override,
    traits: override?.traits ?? base.traits,
    triggerPreferences: override?.triggerPreferences ?? base.triggerPreferences,
    avoidPatterns: override?.avoidPatterns ?? base.avoidPatterns,
    contentFlags: override?.contentFlags ?? base.contentFlags
  };
}

async function materializePersonaDocuments(workspace: AudienceWorkspaceState): Promise<void> {
  const target = resolve(app.getPath("userData"), "audience-modes");
  const staging = resolve(app.getPath("userData"), "audience-modes.tmp");
  await rm(staging, { recursive: true, force: true });
  await mkdir(staging, { recursive: true });
  try {
    await Promise.all(
      workspace.modeState.modes.flatMap((mode) =>
        mode.personaIds.map(async (personaId) => {
          const directory = resolve(staging, mode.id, "personas", personaId);
          const relativeDirectory = relative(staging, directory);
          if (
            !relativeDirectory ||
            relativeDirectory.startsWith("..") ||
            isAbsolute(relativeDirectory)
          ) {
            throw new Error(`拒绝写入非法人格目录：${mode.id}/${personaId}`);
          }
          await mkdir(directory, { recursive: true });
          await writeFile(
            join(directory, "personality.md"),
            serializePersonaMarkdown(mergePersonaForMode(workspace, mode.id, personaId)),
            "utf8"
          );
        })
      )
    );
    await replaceAudienceDirectory(staging, target);
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
}

async function replaceAudienceDirectory(staging: string, target: string): Promise<void> {
  const backup = `${target}.bak`;
  try {
    await rename(staging, target);
    await rm(backup, { recursive: true, force: true });
    return;
  } catch {
    await rm(backup, { recursive: true, force: true });
  }

  let hasBackup = false;
  try {
    await rename(target, backup);
    hasBackup = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }

  try {
    await rename(staging, target);
  } catch (error) {
    if (hasBackup) {
      await rename(backup, target).catch(() => undefined);
    }
    throw error;
  }
  if (hasBackup) await rm(backup, { recursive: true, force: true });
}

async function replaceAudienceWorkspaceFile(temporary: string, target: string): Promise<void> {
  const backup = audienceWorkspaceBackupPath();
  try {
    await rename(temporary, target);
    await rm(backup, { force: true });
    return;
  } catch {
    await rm(backup, { force: true });
  }

  let hasBackup = false;
  try {
    await rename(target, backup);
    hasBackup = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }

  try {
    await rename(temporary, target);
  } catch (error) {
    if (hasBackup) {
      await rename(backup, target).catch(() => undefined);
    }
    throw error;
  }
  if (hasBackup) await rm(backup, { force: true });
}

async function saveAudienceWorkspace(
  candidate: AudienceWorkspaceState
): Promise<SaveAudienceWorkspaceResult> {
  const parsed = parseAudienceWorkspaceState(candidate);
  if (!parsed.ok) {
    throw new Error(`观众配置校验失败：${parsed.issues.slice(0, 3).join("；")}`);
  }

  const target = audienceWorkspacePath();
  const temporary = `${target}.tmp`;
  await mkdir(app.getPath("userData"), { recursive: true });
  await writeFile(temporary, JSON.stringify(parsed.workspace, null, 2), "utf8");
  try {
    await replaceAudienceWorkspaceFile(temporary, target);
  } finally {
    await rm(temporary, { force: true });
  }
  let personaDocumentsSynced = true;
  let personaDocumentsError: string | null = null;
  try {
    await materializePersonaDocuments(parsed.workspace);
  } catch (error) {
    personaDocumentsSynced = false;
    personaDocumentsError =
      error instanceof Error ? error.message : "未知的 personality.md 同步错误";
    const diagnostic = `${new Date().toISOString()} ${personaDocumentsError}\n`;
    await appendFile(
      join(app.getPath("userData"), "audience-persona-sync.log"),
      diagnostic,
      "utf8"
    ).catch(() => undefined);
  }
  return {
    ok: true,
    savedAt: new Date().toISOString(),
    personaDocumentsSynced,
    personaDocumentsError
  };
}

let audienceSaveQueue: Promise<unknown> = Promise.resolve();

function enqueueAudienceWorkspaceSave(
  candidate: AudienceWorkspaceState
): Promise<SaveAudienceWorkspaceResult> {
  const save = audienceSaveQueue.then(
    () => saveAudienceWorkspace(candidate),
    () => saveAudienceWorkspace(candidate)
  );
  audienceSaveQueue = save.catch(() => undefined);
  return save;
}

function getMediaAccessStatus(): MediaAccessSnapshot {
  return {
    microphone: systemPreferences.getMediaAccessStatus("microphone"),
    camera: systemPreferences.getMediaAccessStatus("camera"),
    screen: systemPreferences.getMediaAccessStatus("screen")
  };
}

async function requestMicrophonePermission(): Promise<MediaAccessStatus> {
  if (process.platform === "darwin") {
    await systemPreferences.askForMediaAccess("microphone");
  }
  return systemPreferences.getMediaAccessStatus("microphone");
}

async function requestCameraPermission(): Promise<MediaAccessStatus> {
  if (process.platform === "darwin") {
    await systemPreferences.askForMediaAccess("camera");
  }
  return systemPreferences.getMediaAccessStatus("camera");
}

export function configureMediaAccess(getControlWindow: () => BrowserWindow | null): void {
  const isControlWebContents = (webContents: Electron.WebContents | null): boolean =>
    webContents !== null && webContents.id === getControlWindow()?.webContents.id;

  session.defaultSession.setPermissionCheckHandler((webContents, permission, _origin, details) => {
    if (!isControlWebContents(webContents) || !details.isMainFrame) return false;
    const permissionName: string = permission;
    return (
      permissionName === "display-capture" ||
      (permission === "media" &&
        (details.mediaType === "audio" ||
          (details.mediaType === "video" &&
            hasCameraCaptureAuthorization(webContents?.id ?? -1))))
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
      const isAudioOnly = mediaTypes?.length === 1 && mediaTypes[0] === "audio";
      const isCameraOnly = mediaTypes?.length === 1 && mediaTypes[0] === "video";
      if (permission === "media" && isMainFrame && isCameraOnly) {
        callback(consumeCameraCaptureAuthorization(webContents.id));
        return;
      }
      callback(
        permission === "media" &&
          isMainFrame &&
          (isAudioOnly ||
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

export function registerDesktopIpc(
  getControlWindow: () => BrowserWindow | null,
  confirmControlWindowClose: () => void
): void {
  const assertControlSender = (event: Electron.IpcMainInvokeEvent): void => {
    if (event.sender.id !== getControlWindow()?.webContents.id) {
      throw new Error("Audience configuration is only available to the control window.");
    }
  };

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
  ipcMain.handle("media:request-camera", (event) => {
    assertControlSender(event);
    return requestCameraPermission();
  });
  ipcMain.handle("media:authorize-camera-capture", (event) => {
    assertControlSender(event);
    const cameraStatus = systemPreferences.getMediaAccessStatus("camera");
    if (cameraStatus === "denied" || cameraStatus === "restricted") return false;
    cameraCaptureAuthorization = {
      webContentsId: event.sender.id,
      expiresAt: Date.now() + 15_000
    };
    return true;
  });
  ipcMain.handle("media:cancel-camera-capture-authorization", (event) => {
    assertControlSender(event);
    if (cameraCaptureAuthorization?.webContentsId === event.sender.id) {
      cameraCaptureAuthorization = null;
    }
  });
  ipcMain.handle("overlay:show", showOverlay);
  ipcMain.handle("overlay:hide", hideOverlay);
  ipcMain.handle("overlay:clear", clearOverlay);
  ipcMain.handle("overlay:push", (_event, event: BarrageEvent) => pushBarrage(event));
  ipcMain.handle("config:save-model", (_event, config: ModelConfig) => saveModelConfig(config));
  ipcMain.handle("audience:load-workspace", (event) => {
    assertControlSender(event);
    return loadAudienceWorkspace();
  });
  ipcMain.handle("audience:save-workspace", (event, workspace: AudienceWorkspaceState) => {
    assertControlSender(event);
    return enqueueAudienceWorkspaceSave(workspace);
  });
  ipcMain.handle("app:confirm-close", (event) => {
    assertControlSender(event);
    confirmControlWindowClose();
  });
}
