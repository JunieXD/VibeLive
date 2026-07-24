import { contextBridge, ipcRenderer } from "electron";
import type {
  BackendBarrageEvent,
  BackendRuntimeStatus,
  ControlApi,
  ModelConfig,
  OverlaySettings
} from "../shared/contracts";

const api: ControlApi = {
  listDesktopSources: () => ipcRenderer.invoke("desktop:list-sources"),
  selectDesktopSource: (sourceId) => ipcRenderer.invoke("desktop:select-source", sourceId),
  getMediaAccessStatus: () => ipcRenderer.invoke("media:get-access-status"),
  requestMicrophonePermission: () => ipcRenderer.invoke("media:request-microphone"),
  requestCameraPermission: () => ipcRenderer.invoke("media:request-camera"),
  authorizeCameraCapture: () => ipcRenderer.invoke("media:authorize-camera-capture"),
  cancelCameraCaptureAuthorization: () =>
    ipcRenderer.invoke("media:cancel-camera-capture-authorization"),
  listOverlayTargets: () => ipcRenderer.invoke("overlay:list-targets"),
  getOverlaySettings: () => ipcRenderer.invoke("overlay:get-settings"),
  setOverlaySettings: (settings) => ipcRenderer.invoke("overlay:set-settings", settings),
  showOverlay: () => ipcRenderer.invoke("overlay:show"),
  hideOverlay: () => ipcRenderer.invoke("overlay:hide"),
  clearOverlay: () => ipcRenderer.invoke("overlay:clear"),
  pushBarrage: (event) => ipcRenderer.invoke("overlay:push", event),
  saveModelConfig: (config: ModelConfig) => ipcRenderer.invoke("config:save-model", config),
  getModelConfigStatus: () => ipcRenderer.invoke("config:get-model-status"),
  getBackendStatus: () => ipcRenderer.invoke("backend:get-status"),
  restartBackend: () => ipcRenderer.invoke("backend:restart"),
  startBackendSession: (workspace, clientRequestId) =>
    ipcRenderer.invoke("backend:session-start", workspace, clientRequestId),
  pauseBackendSession: () => ipcRenderer.invoke("backend:session-pause"),
  resumeBackendSession: () => ipcRenderer.invoke("backend:session-resume"),
  stopBackendSession: () => ipcRenderer.invoke("backend:session-stop"),
  queryAudienceRuntime: (sessionId) =>
    ipcRenderer.invoke("backend:runtime-query", sessionId),
  applyAudienceRuntime: (sessionId, workspace, baseRevision) =>
    ipcRenderer.invoke("backend:runtime-apply", sessionId, workspace, baseRevision),
  rollbackAudienceRuntime: (sessionId, baseRevision, targetRevision) =>
    ipcRenderer.invoke(
      "backend:runtime-rollback",
      sessionId,
      baseRevision,
      targetRevision
    ),
  recoverAudienceRuntime: (sessionId) =>
    ipcRenderer.invoke("backend:runtime-recover", sessionId),
  getAudienceRuntimeConfigHash: (workspace, configRevision, room) =>
    ipcRenderer.invoke("backend:runtime-config-hash", workspace, configRevision, room),
  probeAudienceProvider: () => ipcRenderer.invoke("backend:provider-probe"),
  queryDebugTraces: (sessionId, cursor) =>
    ipcRenderer.invoke("backend:debug-traces", sessionId, cursor),
  submitUserText: (text, target) => ipcRenderer.invoke("backend:submit-text", text, target),
  submitAudioSegment: (input) => ipcRenderer.invoke("backend:submit-audio", input),
  submitVisualFrame: (input) => ipcRenderer.invoke("backend:submit-frame", input),
  listRoomMemories: (roomId) => ipcRenderer.invoke("shared-brain:memory-list", roomId),
  getRoomMemoryHead: (roomId) => ipcRenderer.invoke("shared-brain:memory-head", roomId),
  editRoomMemory: (roomId, memoryId, edit) =>
    ipcRenderer.invoke("shared-brain:memory-edit", roomId, memoryId, edit),
  revokeRoomMemory: (roomId, memoryId, expectedRevision) =>
    ipcRenderer.invoke("shared-brain:memory-revoke", roomId, memoryId, expectedRevision),
  deleteRoomMemory: (roomId, memoryId, expectedRevision) =>
    ipcRenderer.invoke("shared-brain:memory-delete", roomId, memoryId, expectedRevision),
  resetRoomMemories: (roomId, expectedRevision) =>
    ipcRenderer.invoke("shared-brain:memory-reset", roomId, expectedRevision),
  listModeMemes: (namespaceId) => ipcRenderer.invoke("shared-brain:meme-list", namespaceId),
  listPendingMemeCandidates: (namespaceId) =>
    ipcRenderer.invoke("shared-brain:meme-candidate-list", namespaceId),
  getModeMemeAutoIngest: (namespaceId) =>
    ipcRenderer.invoke("shared-brain:meme-auto-ingest-get", namespaceId),
  setModeMemeAutoIngest: (namespaceId, enabled, expectedRevision) =>
    ipcRenderer.invoke(
      "shared-brain:meme-auto-ingest-set",
      namespaceId,
      enabled,
      expectedRevision
    ),
  approveMemeCandidate: (namespaceId, candidateId) =>
    ipcRenderer.invoke("shared-brain:meme-candidate-approve", namespaceId, candidateId),
  rejectMemeCandidate: (namespaceId, candidateId) =>
    ipcRenderer.invoke("shared-brain:meme-candidate-reject", namespaceId, candidateId),
  mutateModeMeme: (namespaceId, memeId, action, expectedRevision) =>
    ipcRenderer.invoke(
      "shared-brain:meme-mutate",
      namespaceId,
      memeId,
      action,
      expectedRevision
    ),
  editModeMeme: (namespaceId, memeId, edit) =>
    ipcRenderer.invoke("shared-brain:meme-edit", namespaceId, memeId, edit),
  loadAudienceWorkspace: () => ipcRenderer.invoke("audience:load-workspace"),
  saveAudienceWorkspace: (workspace) =>
    ipcRenderer.invoke("audience:save-workspace", workspace),
  setColorTheme: (theme) => ipcRenderer.invoke("app:set-color-theme", theme),
  confirmCloseAfterAudienceSave: () => ipcRenderer.invoke("app:confirm-close"),
  onCloseRequested: (listener) => {
    const handler = (): void => listener();
    ipcRenderer.on("app:request-close", handler);
    return () => ipcRenderer.removeListener("app:request-close", handler);
  },
  onEmergencyStop: (listener) => {
    const handler = (): void => listener();
    ipcRenderer.on("session:emergency-stop", handler);
    return () => ipcRenderer.removeListener("session:emergency-stop", handler);
  },
  onOverlaySettingsChanged: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, settings: OverlaySettings): void =>
      listener(settings);
    ipcRenderer.on("overlay:settings-changed", handler);
    return () => ipcRenderer.removeListener("overlay:settings-changed", handler);
  },
  onBackendStatus: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, status: BackendRuntimeStatus): void =>
      listener(status);
    ipcRenderer.on("backend:status", handler);
    return () => ipcRenderer.removeListener("backend:status", handler);
  },
  onBackendBarrage: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, event: BackendBarrageEvent): void =>
      listener(event);
    ipcRenderer.on("backend:barrage", handler);
    return () => ipcRenderer.removeListener("backend:barrage", handler);
  }
};

contextBridge.exposeInMainWorld("advx", api);
