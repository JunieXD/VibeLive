import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("advxOverlay", {
  ready: true
});
