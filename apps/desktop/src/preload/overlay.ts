import { contextBridge, ipcRenderer } from "electron";
import type { BarrageEvent, OverlayApi, OverlaySettings } from "../shared/contracts";

const api: OverlayApi = {
  onBarrage: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, barrage: BarrageEvent): void =>
      listener(barrage);
    ipcRenderer.on("overlay:barrage", handler);
    return () => ipcRenderer.removeListener("overlay:barrage", handler);
  },
  onClear: (listener) => {
    const handler = (): void => listener();
    ipcRenderer.on("overlay:clear", handler);
    return () => ipcRenderer.removeListener("overlay:clear", handler);
  },
  onSettingsChanged: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, settings: OverlaySettings): void =>
      listener(settings);
    ipcRenderer.on("overlay:settings-changed", handler);
    return () => ipcRenderer.removeListener("overlay:settings-changed", handler);
  }
};

contextBridge.exposeInMainWorld("advxOverlay", api);
