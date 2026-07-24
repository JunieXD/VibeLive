import { contextBridge, ipcRenderer } from "electron";
import type { BarrageEvent, OverlayApi, OverlaySettings } from "../shared/contracts";

// Sandboxed preloads cannot require Rollup's emitted sibling chunks.
function readOverlayIpcEnvelope<T>(value: unknown): T | null {
  if (
    typeof value !== "object" ||
    value === null ||
    !("protocolVersion" in value) ||
    value.protocolVersion !== 2 ||
    !("payload" in value)
  ) {
    return null;
  }
  return value.payload as T;
}

let settingsListener: ((settings: OverlaySettings) => void) | null = null;
let pendingSettings: OverlaySettings | null = null;

ipcRenderer.on("overlay:settings-changed", (_event, message: unknown) => {
  const settings = readOverlayIpcEnvelope<OverlaySettings>(message);
  if (settings === null) return;
  if (settingsListener) settingsListener(settings);
  else pendingSettings = settings;
});

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
    settingsListener = listener;
    if (pendingSettings) {
      const settings = pendingSettings;
      pendingSettings = null;
      listener(settings);
    }
    return () => {
      if (settingsListener === listener) settingsListener = null;
    };
  }
};

contextBridge.exposeInMainWorld("advxOverlay", api);
