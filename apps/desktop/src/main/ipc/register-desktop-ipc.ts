import {
  app,
  BrowserWindow,
  desktopCapturer,
  ipcMain,
  safeStorage,
  session
} from "electron";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type {
  BarrageEvent,
  DesktopSource,
  ModelConfig,
  SaveModelConfigResult
} from "../../shared/contracts";
import {
  clearOverlay,
  hideOverlay,
  pushBarrage,
  showOverlay
} from "../windows/overlay";

let selectedSourceId: string | null = null;

async function listDesktopSources(controlWindow: BrowserWindow | null): Promise<DesktopSource[]> {
  const sources = await desktopCapturer.getSources({
    types: ["screen", "window"],
    thumbnailSize: { width: 480, height: 270 },
    fetchWindowIcons: true
  });

  return sources
    .filter((source) => source.id !== controlWindow?.getMediaSourceId())
    .map((source) => ({
      id: source.id,
      name: source.name,
      thumbnailUrl: source.thumbnail.toDataURL(),
      appIconUrl: source.appIcon?.isEmpty() === false ? source.appIcon.toDataURL() : null,
      kind: source.id.startsWith("screen:") ? "screen" : "window"
    }));
}

async function saveModelConfig(config: ModelConfig): Promise<SaveModelConfigResult> {
  const configDirectory = app.getPath("userData");
  await mkdir(configDirectory, { recursive: true });

  const storedConfig: Record<string, string> = {
    baseUrl: config.baseUrl.trim(),
    model: config.model.trim()
  };

  let securelyStored = false;
  if (config.apiKey && safeStorage.isEncryptionAvailable()) {
    storedConfig.encryptedApiKey = safeStorage.encryptString(config.apiKey).toString("base64");
    securelyStored = true;
  }

  await writeFile(
    join(configDirectory, "model-config.json"),
    JSON.stringify(storedConfig, null, 2),
    "utf8"
  );
  return { ok: true, securelyStored };
}

export function configureDisplayCapture(): void {
  session.defaultSession.setDisplayMediaRequestHandler(async (_request, callback) => {
    try {
      const sources = await desktopCapturer.getSources({ types: ["screen", "window"] });
      const source = sources.find((candidate) => candidate.id === selectedSourceId);
      callback(source ? { video: source } : {});
    } catch {
      callback({});
    }
  });
}

export function registerDesktopIpc(getControlWindow: () => BrowserWindow | null): void {
  ipcMain.handle("desktop:list-sources", () => listDesktopSources(getControlWindow()));
  ipcMain.handle("desktop:select-source", async (_event, sourceId: string) => {
    const sources = await listDesktopSources(getControlWindow());
    const exists = sources.some((source) => source.id === sourceId);
    selectedSourceId = exists ? sourceId : null;
    return exists;
  });
  ipcMain.handle("overlay:show", showOverlay);
  ipcMain.handle("overlay:hide", hideOverlay);
  ipcMain.handle("overlay:clear", clearOverlay);
  ipcMain.handle("overlay:push", (_event, event: BarrageEvent) => pushBarrage(event));
  ipcMain.handle("config:save-model", (_event, config: ModelConfig) => saveModelConfig(config));
}
