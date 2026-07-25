import {
  app,
  BrowserWindow,
  desktopCapturer,
  ipcMain,
  safeStorage,
  screen,
  session,
  systemPreferences
} from "electron";
import { createHash, randomUUID } from "node:crypto";
import { appendFile, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import {
  materializePersonaTemplate,
  parseAudienceWorkspaceState,
  serializePersonaMarkdown,
  type AudienceWorkspaceState,
  type LegacyLocalMeme
} from "../../shared/audience";
import {
  DEFAULT_ASR_BASE_URL,
  DEFAULT_ASR_MODEL,
  type AudioSource,
  type BackendRuntimeStatus,
  type BarrageEvent,
  type ColorTheme,
  type DesktopSource,
  type MediaAccessSnapshot,
  type MediaAccessStatus,
  type ModelConfig,
  type ModelConfigStatus,
  type OverlaySettings,
  type RuntimeRoomIdentity,
  type SaveAudienceWorkspaceResult,
  type SaveModelConfigResult
} from "../../shared/contracts";
import {
  compileCanonicalRuntimeSpec,
  type AiCallQuery,
  type ModeMemeEdit,
  type RoomMemoryEdit,
  type TextSubmitTarget
} from "../../shared/backend-client";
import { BackendClient } from "../backend/backend-client";
import { formatImageMimeType } from "../backend/realtime-binary";
import {
  clearRuntimeSessionId,
  saveRuntimeSessionId
} from "../backend/runtime-session-state";
import {
  asrProviderChanged,
  configureProviderForSession,
  createRuntimeProviderCandidate,
  mergeProviderProfileSnapshots,
  modelProviderChanged,
  reviseProviderProfileForActiveSession,
  resolveModelConfig,
  resolveModelProvider,
  selectRuntimeProviderConfig,
  type RuntimeProviderIdentity
} from "../model-config";
import {
  migrateLegacyMemes,
  runLegacyMemeMigration
} from "../legacy-meme-migration";
import {
  getOverlaySettings,
  listOverlayTargets,
  setOverlaySettings
} from "../overlay-settings";
import {
  applyBarrageOutputSettings,
  clearBarrageOutputs,
  hideBarrageOutputs,
  pushBarrageToOutputs,
  setBarrageOutputVisibilityListener,
  showBarrageOutputs
} from "../windows/barrage-outputs";
import {
  isFloatingChatSender,
  markFloatingChatRendererReady,
  minimizeFloatingChat
} from "../windows/floating-chat";
import { applyControlWindowTheme } from "../windows/control";

let selectedSourceId: string | null = null;
let displayCaptureAuthorization: { webContentsId: number; expiresAt: number } | null = null;
let cameraCaptureAuthorization: { webContentsId: number; expiresAt: number } | null = null;
let pendingLegacyMemeMigration: {
  raw: string;
  memes: readonly LegacyLocalMeme[];
  recoveryPath: string | null;
} | null = null;

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
  const primaryDisplayId = String(screen.getPrimaryDisplay().id);
  const sourceRank = (source: (typeof sources)[number]): number => {
    if (!source.id.startsWith("screen:")) return 2;
    return source.display_id === primaryDisplayId ? 0 : 1;
  };

  return sources
    .filter((source) => !internalSourceIds.has(source.id))
    .sort((left, right) => sourceRank(left) - sourceRank(right))
    .map((source) => ({
      id: source.id,
      name: source.name,
      thumbnailUrl: source.thumbnail.toDataURL(),
      appIconUrl: source.appIcon?.isEmpty() === false ? source.appIcon.toDataURL() : null,
      kind: source.id.startsWith("screen:") ? "screen" : "window"
    }));
}

async function saveModelConfig(
  config: ModelConfig,
  backendClient: BackendClient
): Promise<SaveModelConfigResult> {
  const configStore = await loadStoredModelConfigStore();
  const stored = configStore?.current ?? null;
  const sessionActive = backendClient.currentStatus().session.state !== "idle";
  const normalized = reviseProviderProfileForActiveSession(
    resolveModelConfig(config, stored),
    stored,
    sessionActive,
    randomUUID()
  );
  const runtimeApplyRequired = sessionActive && modelProviderChanged(normalized, stored);
  const nextSessionRequired = sessionActive && asrProviderChanged(normalized, stored);

  const configDirectory = app.getPath("userData");
  await mkdir(configDirectory, { recursive: true });

  const storedConfig: Record<string, string> = {};

  let securelyStored = false;
  if (safeStorage.isEncryptionAvailable()) {
    const profiles = mergeProviderProfileSnapshots(configStore?.profiles ?? [], normalized);
    storedConfig.encryptedConfig = safeStorage
      .encryptString(JSON.stringify({ current: normalized, profiles }))
      .toString("base64");
    securelyStored = true;
  } else {
    storedConfig.baseUrl = normalized.baseUrl;
    storedConfig.providerProfileId = normalized.providerProfileId;
    storedConfig.model = normalized.model;
    storedConfig.viewerModel = normalized.viewerModel;
    storedConfig.memoryModel = normalized.memoryModel;
    storedConfig.visualSummaryModel = normalized.visualSummaryModel;
    storedConfig.asrBaseUrl = normalized.asrBaseUrl;
    storedConfig.asrModel = normalized.asrModel;
  }

  await writeFile(
    join(configDirectory, "model-config.json"),
    JSON.stringify(storedConfig, null, 2),
    "utf8"
  );

  return {
    ok: true,
    providerProfileId: normalized.providerProfileId,
    securelyStored,
    runtimeApplyRequired,
    nextSessionRequired
  };
}

type ModelConfigStore = {
  current: ModelConfig;
  profiles: ModelConfig[];
};

function parseModelConfigRecord(config: Record<string, unknown>): ModelConfig | null {
  if (
    typeof config.baseUrl !== "string" ||
    typeof config.model !== "string" ||
    typeof config.apiKey !== "string" ||
    typeof config.asrApiKey !== "string"
  ) {
    return null;
  }
  return {
    baseUrl: config.baseUrl,
    providerProfileId:
      typeof config.providerProfileId === "string" ? config.providerProfileId : "default",
    model: config.model,
    viewerModel: typeof config.viewerModel === "string" ? config.viewerModel : "",
    memoryModel: typeof config.memoryModel === "string" ? config.memoryModel : "",
    visualSummaryModel:
      typeof config.visualSummaryModel === "string" ? config.visualSummaryModel : "",
    apiKey: config.apiKey,
    asrBaseUrl:
      typeof config.asrBaseUrl === "string" && config.asrBaseUrl.trim()
        ? config.asrBaseUrl
        : DEFAULT_ASR_BASE_URL,
    asrModel:
      typeof config.asrModel === "string" && config.asrModel.trim()
        ? config.asrModel
        : DEFAULT_ASR_MODEL,
    asrApiKey: config.asrApiKey
  };
}

async function loadStoredModelConfigStore(): Promise<ModelConfigStore | null> {
  let raw: string;
  try {
    raw = await readFile(join(app.getPath("userData"), "model-config.json"), "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  if (!safeStorage.isEncryptionAvailable()) return null;

  const parsed = JSON.parse(raw) as Record<string, unknown>;
  if (typeof parsed.encryptedConfig === "string") {
    try {
      const decrypted = safeStorage.decryptString(
        Buffer.from(parsed.encryptedConfig, "base64")
      );
      const config = JSON.parse(decrypted) as Record<string, unknown>;
      if (typeof config.current === "object" && config.current !== null) {
        const current = parseModelConfigRecord(config.current as Record<string, unknown>);
        const profiles = Array.isArray(config.profiles)
          ? config.profiles.flatMap((profile) => {
              if (typeof profile !== "object" || profile === null) return [];
              const parsedProfile = parseModelConfigRecord(profile as Record<string, unknown>);
              return parsedProfile ? [parsedProfile] : [];
            })
          : [];
        if (current) {
          return {
            current,
            profiles: mergeProviderProfileSnapshots(profiles, current)
          };
        }
      }
      const legacy = parseModelConfigRecord(config);
      if (legacy) return { current: legacy, profiles: [legacy] };
    } catch {
      return null;
    }
  }
  const encryptedModelApiKey =
    typeof parsed.encryptedModelApiKey === "string"
      ? parsed.encryptedModelApiKey
      : typeof parsed.encryptedApiKey === "string"
        ? parsed.encryptedApiKey
        : null;
  if (
    typeof parsed.baseUrl !== "string" ||
    typeof parsed.model !== "string" ||
    encryptedModelApiKey === null ||
    typeof parsed.encryptedAsrApiKey !== "string"
  ) {
    return null;
  }
  try {
    const current = {
      baseUrl: parsed.baseUrl,
      providerProfileId: "default",
      model: parsed.model,
      viewerModel: "",
      memoryModel: "",
      visualSummaryModel: "",
      apiKey: safeStorage.decryptString(Buffer.from(encryptedModelApiKey, "base64")),
      asrBaseUrl:
        typeof parsed.asrBaseUrl === "string" && parsed.asrBaseUrl.trim()
          ? parsed.asrBaseUrl
          : DEFAULT_ASR_BASE_URL,
      asrModel:
        typeof parsed.asrModel === "string" && parsed.asrModel.trim()
          ? parsed.asrModel
          : DEFAULT_ASR_MODEL,
      asrApiKey: safeStorage.decryptString(Buffer.from(parsed.encryptedAsrApiKey, "base64"))
    };
    return { current, profiles: [current] };
  } catch {
    return null;
  }
}

async function loadStoredModelConfig(): Promise<ModelConfig | null> {
  return (await loadStoredModelConfigStore())?.current ?? null;
}

async function loadRuntimeProviderConfig(
  target: RuntimeProviderIdentity
): Promise<ModelConfig> {
  const store = await loadStoredModelConfigStore();
  if (!store) {
    throw new Error(`缺少供应商 ${target.provider_profile_id} 的安全凭据快照，已阻止运行时切换。`);
  }
  return selectRuntimeProviderConfig(store.profiles, target);
}

async function getStoredModelConfigStatus(): Promise<ModelConfigStatus> {
  const config = await loadStoredModelConfig();
  if (!config) {
    return {
      baseUrl: null,
      providerProfileId: null,
      model: null,
      viewerModel: null,
      memoryModel: null,
      visualSummaryModel: null,
      asrBaseUrl: null,
      asrModel: null,
      modelApiKeyStored: false,
      asrApiKeyStored: false
    };
  }
  return {
    baseUrl: config.baseUrl,
    providerProfileId: config.providerProfileId,
    model: config.model,
    viewerModel: config.viewerModel || null,
    memoryModel: config.memoryModel || null,
    visualSummaryModel: config.visualSummaryModel || null,
    asrBaseUrl: config.asrBaseUrl,
    asrModel: config.asrModel,
    modelApiKeyStored: true,
    asrApiKeyStored: true
  };
}

export async function configureCurrentProviderForSession(
  backendClient: BackendClient,
  restartBackend: () => Promise<BackendRuntimeStatus>
): Promise<boolean> {
  const config = await loadStoredModelConfig();
  if (!config) return false;
  await configureProviderForSession(config, backendClient, restartBackend);
  return true;
}

async function compileAudienceRuntime(
  workspace: AudienceWorkspaceState,
  configRevision: number,
  room?: RuntimeRoomIdentity
) {
  const modelConfig = await loadStoredModelConfig();
  if (!modelConfig) throw new Error("请先保存模型配置，再启动或应用观众运行时。");
  const provider = resolveModelProvider(modelConfig);
  return compileCanonicalRuntimeSpec(workspace, {
    configRevision,
    provider: {
      providerProfileId: provider.providerProfileId,
      viewerModel: provider.viewerModel,
      memoryModel: provider.memoryModel,
      visualSummaryModel: provider.visualSummaryModel
    },
    roomId: room?.roomId,
    roomDisplayName: room?.displayName,
    roomRevision: room?.revision
  });
}

async function loadRuntimeProviderCandidate(target: RuntimeProviderIdentity) {
  return createRuntimeProviderCandidate(await loadRuntimeProviderConfig(target));
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
  if (parsed.legacyMemes?.length) {
    pendingLegacyMemeMigration = {
      raw,
      memes: parsed.legacyMemes,
      recoveryPath: await preserveRejectedAudienceWorkspace(raw)
    };
  } else {
    pendingLegacyMemeMigration = null;
  }
  return parsed.workspace;
}

function mergePersonaForMode(
  workspace: AudienceWorkspaceState,
  modeId: string,
  personaId: string
) {
  const base = workspace.personas.find((persona) => persona.id === personaId);
  const mode = workspace.modeState.modes.find((candidate) => candidate.id === modeId);
  if (!base || !mode) throw new Error(`无法生成模式 ${modeId} 的人格 ${personaId}`);
  return materializePersonaTemplate(base, mode.personaOverrides[personaId]);
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
  if (pendingLegacyMemeMigration) {
    throw new Error(
      pendingLegacyMemeMigration.recoveryPath
        ? `旧版本地梗尚未迁移到 Shared Brain。原内容已保留在 ${pendingLegacyMemeMigration.recoveryPath}`
        : "旧版本地梗尚未迁移到 Shared Brain，原配置文件未被覆盖。"
    );
  }
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
    screen: systemPreferences.getMediaAccessStatus("screen"),
    systemAudioSupported: process.platform === "win32"
  };
}

function isAudioSource(value: unknown): value is AudioSource {
  return value === "microphone" || value === "system_audio";
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

export function configureMediaAccess(
  getControlWindow: () => BrowserWindow | null,
  platform: NodeJS.Platform = process.platform
): void {
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
      const controlWindow = getControlWindow();
      const controlFrame = controlWindow?.webContents.mainFrame;
      if (
        !hasDisplayCaptureAuthorization(controlWindow?.webContents.id ?? -1) ||
        !request.videoRequested ||
        request.frame?.frameTreeNodeId !== controlFrame?.frameTreeNodeId
      ) {
        displayCaptureAuthorization = null;
        callback({});
        return;
      }

      displayCaptureAuthorization = null;
      const sources = await desktopCapturer.getSources({ types: ["screen", "window"] });
      const source = sources.find((candidate) => candidate.id === selectedSourceId);
      if (!source) {
        callback({});
        return;
      }
      callback({
        video: source,
        ...(request.audioRequested && platform === "win32"
          ? { audio: "loopback" as const }
          : {})
      });
    } catch {
      displayCaptureAuthorization = null;
      callback({});
    }
  });
}

function applyOverlayWindowState(
  getControlWindow: () => BrowserWindow | null,
  settings: OverlaySettings
): void {
  const controlWindow = getControlWindow();
  applyBarrageOutputSettings(settings);

  if (controlWindow && !controlWindow.isDestroyed()) {
    controlWindow.setAlwaysOnTop(false);
  }
}

export function broadcastOverlaySettings(
  getControlWindow: () => BrowserWindow | null,
  settings: OverlaySettings
): void {
  applyOverlayWindowState(getControlWindow, settings);
  const controlWindow = getControlWindow();
  if (controlWindow && !controlWindow.isDestroyed()) {
    controlWindow.webContents.send("overlay:settings-changed", settings);
  }
}

export function registerDesktopIpc(
  getControlWindow: () => BrowserWindow | null,
  confirmControlWindowClose: () => void,
  backendClient: BackendClient,
  restartBackend: () => Promise<BackendRuntimeStatus>
): void {
  const assertControlSender = (event: Electron.IpcMainInvokeEvent): void => {
    if (event.sender.id !== getControlWindow()?.webContents.id) {
      throw new Error("This API is only available to the control window.");
    }
  };
  const assertFloatingChatSender = (event: Electron.IpcMainInvokeEvent): void => {
    if (!isFloatingChatSender(event.sender.id)) {
      throw new Error("This API is only available to the floating chat window.");
    }
  };
  const assertTextSender = (event: Electron.IpcMainInvokeEvent): void => {
    if (
      event.sender.id !== getControlWindow()?.webContents.id &&
      !isFloatingChatSender(event.sender.id)
    ) {
      throw new Error("This API is only available to an ADVX interaction window.");
    }
  };

  setBarrageOutputVisibilityListener((visible) => {
    const controlWindow = getControlWindow();
    if (controlWindow && !controlWindow.isDestroyed()) {
      controlWindow.webContents.send("overlay:visibility-changed", visible);
    }
  });

  backendClient.onStatus((status) => {
    const controlWindow = getControlWindow();
    if (controlWindow && !controlWindow.isDestroyed()) {
      controlWindow.webContents.send("backend:status", status);
    }
  });
  backendClient.onBarrage((event) => {
    const controlWindow = getControlWindow();
    if (controlWindow && !controlWindow.isDestroyed()) {
      controlWindow.webContents.send("backend:barrage", event);
    }
  });
  backendClient.onViewerEvent((event) => {
    const controlWindow = getControlWindow();
    if (controlWindow && !controlWindow.isDestroyed()) {
      controlWindow.webContents.send("backend:viewer-event", event);
    }
  });
  backendClient.onTranscript((event) => {
    const controlWindow = getControlWindow();
    if (controlWindow && !controlWindow.isDestroyed()) {
      controlWindow.webContents.send("backend:transcript", event);
    }
  });

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
  ipcMain.handle("overlay:list-targets", (event) => {
    assertControlSender(event);
    return listOverlayTargets();
  });
  ipcMain.handle("overlay:get-settings", (event) => {
    assertControlSender(event);
    return getOverlaySettings();
  });
  ipcMain.handle("overlay:set-settings", async (event, settings: OverlaySettings) => {
    assertControlSender(event);
    const savedSettings = await setOverlaySettings(settings);
    applyOverlayWindowState(getControlWindow, savedSettings);
    return savedSettings;
  });
  ipcMain.handle("overlay:show", (event) => {
    assertControlSender(event);
    return showBarrageOutputs();
  });
  ipcMain.handle("overlay:hide", (event) => {
    assertControlSender(event);
    hideBarrageOutputs();
  });
  ipcMain.handle("overlay:clear", (event) => {
    assertControlSender(event);
    clearBarrageOutputs();
  });
  ipcMain.handle("overlay:push", (event, barrage: BarrageEvent) => {
    assertControlSender(event);
    return pushBarrageToOutputs(barrage);
  });
  ipcMain.handle("floating-chat:minimize", (event) => {
    assertFloatingChatSender(event);
    minimizeFloatingChat();
  });
  ipcMain.handle("floating-chat:hide", (event) => {
    assertFloatingChatSender(event);
    hideBarrageOutputs();
  });
  ipcMain.handle("floating-chat:clear", (event) => {
    assertFloatingChatSender(event);
    clearBarrageOutputs();
  });
  ipcMain.on("floating-chat:ready", (event) => {
    markFloatingChatRendererReady(event.sender.id);
  });
  ipcMain.handle("config:save-model", (event, config: ModelConfig) => {
    assertControlSender(event);
    return saveModelConfig(config, backendClient);
  });
  ipcMain.handle("config:get-model-status", (event) => {
    assertControlSender(event);
    return getStoredModelConfigStatus();
  });
  ipcMain.handle("backend:get-status", (event) => {
    assertControlSender(event);
    return backendClient.status();
  });
  ipcMain.handle("backend:restart", (event) => {
    assertControlSender(event);
    return restartBackend();
  });
  ipcMain.handle(
    "backend:session-start",
    async (event, workspace: AudienceWorkspaceState, clientRequestId: string) => {
      assertControlSender(event);
      if (typeof clientRequestId !== "string" || !clientRequestId.trim()) {
        throw new Error("client_request_id 无效。");
      }
      const parsed = parseAudienceWorkspaceState(workspace);
      if (!parsed.ok) throw new Error(parsed.issues.join("; "));
      if (!(await configureCurrentProviderForSession(backendClient, restartBackend))) {
        throw new Error("请先保存模型配置，再启动观众运行时。");
      }
      const compiled = await compileAudienceRuntime(parsed.workspace, 1);
      const started = await backendClient.startSession(
        clientRequestId,
        compiled
      );
      if (pendingLegacyMemeMigration && started.sessionId) {
        const migration = pendingLegacyMemeMigration;
        await runLegacyMemeMigration({
          sessionId: started.sessionId,
          migrate: async () => {
            try {
              const activeMode = compiled.spec.modes.find(
                (mode) => mode.mode_id === compiled.spec.active_mode_id
              );
              if (!activeMode) {
                throw new Error("当前运行时缺少激活 Mode。");
              }
              const runtime = await backendClient.queryRuntime(started.sessionId as string);
              await migrateLegacyMemes(
                migration.memes,
                {
                  roomId: runtime.room_id,
                  sessionId: runtime.session_id,
                  audienceEpoch: runtime.audience_epoch,
                  namespaceId: activeMode.namespace_id
                },
                backendClient
              );
            } catch (error) {
              const reason = error instanceof Error ? error.message : "未知迁移错误";
              throw new Error(
                migration.recoveryPath
                  ? `${reason} 原始 v1 配置保留在 ${migration.recoveryPath}`
                  : `${reason} 原始 v1 配置仍保留在原路径。`
              );
            }
          },
          persistWorkspace: async () => {
            pendingLegacyMemeMigration = null;
            try {
              await saveAudienceWorkspace(parsed.workspace);
            } catch (error) {
              pendingLegacyMemeMigration = migration;
              throw error;
            }
          },
          saveRecoverySession: () =>
            saveRuntimeSessionId(app.getPath("userData"), started.sessionId as string),
          clearRecoverySession: () => clearRuntimeSessionId(app.getPath("userData")),
          stopSession: () => backendClient.stopSession()
        });
      }
      if (started.sessionId) {
        await saveRuntimeSessionId(app.getPath("userData"), started.sessionId);
      }
      return started;
    }
  );
  ipcMain.handle("backend:session-pause", (event) => {
    assertControlSender(event);
    return backendClient.pauseSession();
  });
  ipcMain.handle("backend:session-resume", (event) => {
    assertControlSender(event);
    return backendClient.resumeSession();
  });
  ipcMain.handle("backend:session-stop", async (event) => {
    assertControlSender(event);
    const stopped = await backendClient.stopSession();
    await clearRuntimeSessionId(app.getPath("userData"));
    return stopped;
  });
  ipcMain.handle("backend:submit-text", (event, text: string, target?: TextSubmitTarget) => {
    assertTextSender(event);
    if (typeof text !== "string" || !text.trim() || text.length > 4_000) {
      throw new Error("文字输入无效。");
    }
    if (target?.targetViewerId && target?.targetPersonaId) {
      throw new Error("文字输入不能同时指定 Viewer 和 Persona。");
    }
    return backendClient.submitText(`text-${randomUUID()}`, Date.now(), text.trim(), target);
  });
  ipcMain.handle(
    "backend:submit-audio",
    (
      event,
      input: {
        inputId: string;
        capturedAtMs: number;
        body: Uint8Array;
        source: AudioSource;
        turnId?: string;
        systemAudioRequired?: boolean;
      }
    ) => {
      assertControlSender(event);
      if (!isAudioSource(input.source)) throw new Error("音频来源无效。");
      if (input.turnId !== undefined && (!input.turnId || input.turnId.length > 128)) {
        throw new Error("语音轮次无效。");
      }
      if (
        input.systemAudioRequired !== undefined &&
        typeof input.systemAudioRequired !== "boolean"
      ) {
        throw new Error("系统声音轮次标记无效。");
      }
      if (
        input.systemAudioRequired &&
        input.source !== "microphone"
      ) {
        throw new Error("系统声音轮次必须由麦克风发起。");
      }
      return backendClient.submitAudioSegment(input);
    }
  );
  ipcMain.on(
    "backend:voice-activity",
    (event, source: AudioSource, occurredAtMs: number) => {
      assertControlSender(event);
      if (!Number.isInteger(occurredAtMs) || occurredAtMs < 0 || !isAudioSource(source)) {
        return;
      }
      backendClient.notifyVoiceActivity(source, occurredAtMs);
    }
  );
  ipcMain.handle(
    "backend:submit-frame",
    (
      event,
      input: {
        inputId: string;
        capturedAtMs: number;
        mimeType: string;
        changeScore: number;
        body: Uint8Array;
      }
    ) => {
      assertControlSender(event);
      return backendClient.submitFrame({
        inputId: input.inputId,
        capturedAtMs: input.capturedAtMs,
        mimeType: formatImageMimeType(input.mimeType, input.changeScore),
        body: input.body
      });
    }
  );
  ipcMain.handle("audience:load-workspace", (event) => {
    assertControlSender(event);
    return loadAudienceWorkspace();
  });
  ipcMain.handle("audience:save-workspace", (event, workspace: AudienceWorkspaceState) => {
    assertControlSender(event);
    return enqueueAudienceWorkspaceSave(workspace);
  });
  ipcMain.handle("backend:runtime-query", (event, sessionId: string) => {
    assertControlSender(event);
    return backendClient.queryRuntime(sessionId);
  });
  ipcMain.handle("backend:audience-query", (event, sessionId: string) => {
    assertControlSender(event);
    return backendClient.queryAudience(sessionId);
  });
  ipcMain.handle(
    "backend:viewer-mute",
    (event, sessionId: string, viewerId: string, durationMs: number, reason?: string) => {
      assertControlSender(event);
      return backendClient.muteViewer(sessionId, viewerId, durationMs, reason);
    }
  );
  ipcMain.handle("backend:viewer-unmute", (event, sessionId: string, viewerId: string) => {
    assertControlSender(event);
    return backendClient.unmuteViewer(sessionId, viewerId);
  });
  ipcMain.handle(
    "backend:viewer-kick",
    (event, sessionId: string, viewerId: string, reason?: string) => {
      assertControlSender(event);
      return backendClient.kickViewer(sessionId, viewerId, reason);
    }
  );
  ipcMain.handle(
    "backend:runtime-apply",
    async (
      event,
      sessionId: string,
      workspace: AudienceWorkspaceState,
      baseRevision: number
    ) => {
      assertControlSender(event);
      const parsed = parseAudienceWorkspaceState(workspace);
      if (!parsed.ok) throw new Error(parsed.issues.join("; "));
      const compiled = await compileAudienceRuntime(parsed.workspace, baseRevision + 1);
      return backendClient.applyRuntime(
        sessionId,
        `apply-${randomUUID()}`,
        baseRevision,
        compiled,
        await loadRuntimeProviderCandidate(compiled.spec.provider)
      );
    }
  );
  ipcMain.handle(
    "backend:runtime-rollback",
    async (event, sessionId: string, baseRevision: number, targetRevision: number) => {
      assertControlSender(event);
      const targetProvider = backendClient.runtimeProviderAtRevision(
        sessionId,
        targetRevision
      );
      if (!targetProvider) {
        throw new Error(`无法读取 runtime revision ${targetRevision} 的供应商，已阻止回滚。`);
      }
      return backendClient.rollbackRuntime(
        sessionId,
        `rollback-${randomUUID()}`,
        baseRevision,
        targetRevision,
        await loadRuntimeProviderCandidate(targetProvider)
      );
    }
  );
  ipcMain.handle("backend:runtime-recover", async (event, sessionId: string) => {
    assertControlSender(event);
    if (typeof sessionId !== "string" || !sessionId.trim()) {
      throw new Error("runtime session ID 无效。");
    }
    const persisted = await backendClient.queryRuntime(sessionId);
    const providerConfig = await loadRuntimeProviderConfig(
      persisted.canonical_runtime_spec.provider
    );
    await configureProviderForSession(providerConfig, backendClient, restartBackend);
    const recovered = await backendClient.recoverRuntime(sessionId);
    await saveRuntimeSessionId(app.getPath("userData"), recovered.session_id);
    return recovered;
  });
  ipcMain.handle(
    "backend:runtime-config-hash",
    async (
      event,
      workspace: AudienceWorkspaceState,
      configRevision: number,
      room: RuntimeRoomIdentity
    ) => {
      assertControlSender(event);
      const parsed = parseAudienceWorkspaceState(workspace);
      if (!parsed.ok) throw new Error(parsed.issues.join("; "));
      return (await compileAudienceRuntime(parsed.workspace, configRevision, room)).configHash;
    }
  );
  ipcMain.handle("backend:provider-probe", (event) => {
    assertControlSender(event);
    return backendClient.probeProvider();
  });
  ipcMain.handle("backend:debug-traces", (event, sessionId: string, cursor?: string) => {
    assertControlSender(event);
    return backendClient.queryDebugTraces(sessionId, cursor);
  });
  ipcMain.handle("backend:ai-calls", (event, query: AiCallQuery) => {
    assertControlSender(event);
    return backendClient.queryAiCalls(query);
  });
  ipcMain.handle("shared-brain:memory-list", (event, roomId: string) => {
    assertControlSender(event);
    return backendClient.listRoomMemories(roomId);
  });
  ipcMain.handle("shared-brain:memory-head", (event, roomId: string) => {
    assertControlSender(event);
    return backendClient.getRoomMemoryHead(roomId);
  });
  ipcMain.handle(
    "shared-brain:memory-edit",
    (event, roomId: string, memoryId: string, edit: RoomMemoryEdit) => {
      assertControlSender(event);
      return backendClient.editRoomMemory(roomId, memoryId, edit);
    }
  );
  ipcMain.handle(
    "shared-brain:memory-revoke",
    (event, roomId: string, memoryId: string, expectedRevision: number) => {
      assertControlSender(event);
      return backendClient.revokeRoomMemory(roomId, memoryId, expectedRevision);
    }
  );
  ipcMain.handle(
    "shared-brain:memory-delete",
    (event, roomId: string, memoryId: string, expectedRevision: number) => {
      assertControlSender(event);
      return backendClient.deleteRoomMemory(roomId, memoryId, expectedRevision);
    }
  );
  ipcMain.handle(
    "shared-brain:memory-reset",
    (event, roomId: string, expectedRevision: number) => {
      assertControlSender(event);
      return backendClient.resetRoomMemories(roomId, expectedRevision);
    }
  );
  ipcMain.handle("shared-brain:meme-list", (event, namespaceId: string) => {
    assertControlSender(event);
    return backendClient.listModeMemes(namespaceId);
  });
  ipcMain.handle("shared-brain:meme-candidate-list", (event, namespaceId: string) => {
    assertControlSender(event);
    return backendClient.listPendingMemeCandidates(namespaceId);
  });
  ipcMain.handle("shared-brain:meme-auto-ingest-get", (event, namespaceId: string) => {
    assertControlSender(event);
    return backendClient.getModeMemeAutoIngest(namespaceId);
  });
  ipcMain.handle(
    "shared-brain:meme-auto-ingest-set",
    (event, namespaceId: string, enabled: boolean, expectedRevision: number) => {
      assertControlSender(event);
      return backendClient.setModeMemeAutoIngest(namespaceId, enabled, expectedRevision);
    }
  );
  ipcMain.handle(
    "shared-brain:meme-candidate-approve",
    (event, namespaceId: string, candidateId: string) => {
      assertControlSender(event);
      return backendClient.approveMemeCandidate(namespaceId, candidateId);
    }
  );
  ipcMain.handle(
    "shared-brain:meme-candidate-reject",
    (event, namespaceId: string, candidateId: string) => {
      assertControlSender(event);
      return backendClient.rejectMemeCandidate(namespaceId, candidateId);
    }
  );
  ipcMain.handle(
    "shared-brain:meme-mutate",
    (
      event,
      namespaceId: string,
      memeId: string,
      action: "undo" | "revoke" | "disable" | "restore" | "pin" | "unpin" | "archive" | "restart",
      expectedRevision: number
    ) => {
      assertControlSender(event);
      const supported = new Set([
        "undo",
        "revoke",
        "disable",
        "restore",
        "pin",
        "unpin",
        "archive",
        "restart"
      ]);
      if (!supported.has(action)) throw new Error("不支持的梗库操作。");
      return backendClient.mutateModeMeme(
        namespaceId,
        memeId,
        action,
        expectedRevision
      );
    }
  );
  ipcMain.handle(
    "shared-brain:meme-edit",
    (event, namespaceId: string, memeId: string, edit: ModeMemeEdit) => {
      assertControlSender(event);
      return backendClient.editModeMeme(namespaceId, memeId, edit);
    }
  );
  ipcMain.handle("app:set-color-theme", (event, theme: ColorTheme) => {
    assertControlSender(event);
    if (theme !== "light" && theme !== "dark") {
      throw new Error("Unsupported control window theme.");
    }
    const controlWindow = getControlWindow();
    if (controlWindow && !controlWindow.isDestroyed()) {
      applyControlWindowTheme(controlWindow, theme);
    }
  });
  ipcMain.handle("app:confirm-close", (event) => {
    assertControlSender(event);
    confirmControlWindowClose();
  });
}
