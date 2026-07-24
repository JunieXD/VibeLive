import { contextBridge, ipcRenderer } from "electron";
import type {
  BarrageEvent,
  FloatingChatApi
} from "../shared/contracts";

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

let barrageListener: ((event: BarrageEvent) => void) | null = null;
let clearListener: (() => void) | null = null;
let pendingBarrage: BarrageEvent[] = [];
let pendingClear = false;

ipcRenderer.on("overlay:barrage", (_event, message: unknown) => {
  const barrage = readOverlayIpcEnvelope<BarrageEvent>(message);
  if (barrage === null) return;
  if (barrageListener) barrageListener(barrage);
  else pendingBarrage.push(barrage);
});

ipcRenderer.on("overlay:clear", (_event, message: unknown) => {
  if (readOverlayIpcEnvelope<boolean>(message) !== true) return;
  pendingBarrage = [];
  if (clearListener) clearListener();
  else pendingClear = true;
});

const api: FloatingChatApi = {
  onBarrage: (listener) => {
    barrageListener = listener;
    const queued = pendingBarrage;
    pendingBarrage = [];
    for (const barrage of queued) listener(barrage);
    return () => {
      if (barrageListener === listener) barrageListener = null;
    };
  },
  onClear: (listener) => {
    clearListener = listener;
    if (pendingClear) {
      pendingClear = false;
      listener();
    }
    return () => {
      if (clearListener === listener) clearListener = null;
    };
  },
  minimize: () => ipcRenderer.invoke("floating-chat:minimize"),
  hide: () => ipcRenderer.invoke("floating-chat:hide"),
  clear: () => ipcRenderer.invoke("floating-chat:clear"),
  submitText: (text) => ipcRenderer.invoke("backend:submit-text", text)
};

contextBridge.exposeInMainWorld("advxFloatingChat", api);
ipcRenderer.send("floating-chat:ready");
