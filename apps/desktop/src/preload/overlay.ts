import { contextBridge, ipcRenderer } from "electron";
import type { BarrageEvent, OverlayApi, OverlaySettings } from "../shared/contracts";
import { readOverlayIpcEnvelope } from "../shared/overlay-protocol";

const api: OverlayApi = {
  onBarrage: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, message: unknown): void => {
      const barrage = readOverlayIpcEnvelope<BarrageEvent>(message);
      if (barrage !== null) listener(barrage);
    };
    ipcRenderer.on("overlay:barrage", handler);
    return () => ipcRenderer.removeListener("overlay:barrage", handler);
  },
  onClear: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, message: unknown): void => {
      if (readOverlayIpcEnvelope<boolean>(message) === true) listener();
    };
    ipcRenderer.on("overlay:clear", handler);
    return () => ipcRenderer.removeListener("overlay:clear", handler);
  },
  onSettingsChanged: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, message: unknown): void => {
      const settings = readOverlayIpcEnvelope<OverlaySettings>(message);
      if (settings !== null) listener(settings);
    };
    ipcRenderer.on("overlay:settings-changed", handler);
    return () => ipcRenderer.removeListener("overlay:settings-changed", handler);
  }
};

contextBridge.exposeInMainWorld("advxOverlay", api);
