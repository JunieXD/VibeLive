import { contextBridge, ipcRenderer } from "electron";
import type { BarrageEvent, OverlayApi } from "../shared/contracts";

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
  }
};

contextBridge.exposeInMainWorld("advxOverlay", api);
