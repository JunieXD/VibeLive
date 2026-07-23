import { contextBridge, ipcRenderer } from "electron";
import type { ControlApi, ModelConfig, OverlaySettings } from "../shared/contracts";

const api: ControlApi = {
  listDesktopSources: () => ipcRenderer.invoke("desktop:list-sources"),
  selectDesktopSource: (sourceId) => ipcRenderer.invoke("desktop:select-source", sourceId),
  getMediaAccessStatus: () => ipcRenderer.invoke("media:get-access-status"),
  requestMicrophonePermission: () => ipcRenderer.invoke("media:request-microphone"),
  listOverlayTargets: () => ipcRenderer.invoke("overlay:list-targets"),
  getOverlaySettings: () => ipcRenderer.invoke("overlay:get-settings"),
  setOverlaySettings: (settings) => ipcRenderer.invoke("overlay:set-settings", settings),
  showOverlay: () => ipcRenderer.invoke("overlay:show"),
  hideOverlay: () => ipcRenderer.invoke("overlay:hide"),
  clearOverlay: () => ipcRenderer.invoke("overlay:clear"),
  pushBarrage: (event) => ipcRenderer.invoke("overlay:push", event),
  saveModelConfig: (config: ModelConfig) => ipcRenderer.invoke("config:save-model", config),
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
  }
};

contextBridge.exposeInMainWorld("advx", api);
