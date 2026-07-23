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
  startBackendSession: () => ipcRenderer.invoke("backend:session-start"),
  pauseBackendSession: () => ipcRenderer.invoke("backend:session-pause"),
  resumeBackendSession: () => ipcRenderer.invoke("backend:session-resume"),
  stopBackendSession: () => ipcRenderer.invoke("backend:session-stop"),
  submitUserText: (text) => ipcRenderer.invoke("backend:submit-text", text),
  submitAudioSegment: (input) => ipcRenderer.invoke("backend:submit-audio", input),
  submitVisualFrame: (input) => ipcRenderer.invoke("backend:submit-frame", input),
  loadAudienceWorkspace: () => ipcRenderer.invoke("audience:load-workspace"),
  saveAudienceWorkspace: (workspace) =>
    ipcRenderer.invoke("audience:save-workspace", workspace),
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
