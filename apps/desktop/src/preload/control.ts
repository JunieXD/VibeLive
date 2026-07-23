import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("advx", {
  platform: process.platform,
  versions: process.versions
});
