import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("advxCapture", {
  ready: true
});
