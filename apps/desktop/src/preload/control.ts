import { contextBridge, ipcRenderer } from "electron";
import type { ControlApi, ModelConfig } from "../shared/contracts";

const api: ControlApi = {
  listDesktopSources: () => ipcRenderer.invoke("desktop:list-sources"),
  selectDesktopSource: (sourceId) => ipcRenderer.invoke("desktop:select-source", sourceId),
  getMediaAccessStatus: () => ipcRenderer.invoke("media:get-access-status"),
  requestMicrophonePermission: () => ipcRenderer.invoke("media:request-microphone"),
  requestCameraPermission: () => ipcRenderer.invoke("media:request-camera"),
  authorizeCameraCapture: () => ipcRenderer.invoke("media:authorize-camera-capture"),
  cancelCameraCaptureAuthorization: () =>
    ipcRenderer.invoke("media:cancel-camera-capture-authorization"),
  showOverlay: () => ipcRenderer.invoke("overlay:show"),
  hideOverlay: () => ipcRenderer.invoke("overlay:hide"),
  clearOverlay: () => ipcRenderer.invoke("overlay:clear"),
  pushBarrage: (event) => ipcRenderer.invoke("overlay:push", event),
  saveModelConfig: (config: ModelConfig) => ipcRenderer.invoke("config:save-model", config),
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
  }
};

contextBridge.exposeInMainWorld("advx", api);
