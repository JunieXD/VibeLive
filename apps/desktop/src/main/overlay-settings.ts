import { app, screen } from "electron";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { OverlaySettings, OverlayTarget } from "../shared/contracts";
import {
  createDefaultOverlaySettings,
  normalizeOverlaySettings
} from "./overlay-settings-validation";

let currentSettings: OverlaySettings | null = null;
let settingsTransaction: Promise<void> = Promise.resolve();

function getDisplayContext(): {
  primaryDisplayId: number;
  displayIds: number[];
} {
  const primaryDisplayId = screen.getPrimaryDisplay().id;
  return {
    primaryDisplayId,
    displayIds: screen.getAllDisplays().map((display) => display.id)
  };
}

function getSettingsPath(): string {
  return join(app.getPath("userData"), "overlay-config.json");
}

async function persistOverlaySettings(settings: OverlaySettings): Promise<void> {
  await mkdir(app.getPath("userData"), { recursive: true });
  const settingsPath = getSettingsPath();
  const temporaryPath = `${settingsPath}.${process.pid}.tmp`;
  try {
    await writeFile(temporaryPath, JSON.stringify(settings, null, 2), "utf8");
    await rename(temporaryPath, settingsPath);
  } catch (error) {
    await rm(temporaryPath, { force: true }).catch(() => undefined);
    throw error;
  }
}

function runSettingsTransaction<T>(operation: () => Promise<T>): Promise<T> {
  const result = settingsTransaction.then(operation);
  settingsTransaction = result.then(
    () => undefined,
    () => undefined
  );
  return result;
}

function isMissingFileError(error: unknown): boolean {
  return (
    error instanceof Error &&
    "code" in error &&
    (error as NodeJS.ErrnoException).code === "ENOENT"
  );
}

export function listOverlayTargets(): OverlayTarget[] {
  const primaryDisplayId = screen.getPrimaryDisplay().id;
  return screen.getAllDisplays().map((display, index) => ({
    id: display.id,
    name: display.label || `Display ${index + 1}`,
    bounds: { ...display.bounds },
    scaleFactor: display.scaleFactor,
    isPrimary: display.id === primaryDisplayId
  }));
}

export async function initializeOverlaySettings(): Promise<OverlaySettings> {
  const { primaryDisplayId, displayIds } = getDisplayContext();

  try {
    const stored = JSON.parse(await readFile(getSettingsPath(), "utf8")) as unknown;
    currentSettings =
      normalizeOverlaySettings(stored, primaryDisplayId, displayIds) ??
      createDefaultOverlaySettings(primaryDisplayId);
  } catch (error) {
    if (!isMissingFileError(error)) {
      console.error("Failed to read Overlay settings; using defaults", error);
    }
    currentSettings = createDefaultOverlaySettings(primaryDisplayId);
  }

  return currentSettings;
}

export function getOverlaySettings(): OverlaySettings {
  if (!currentSettings) {
    currentSettings = createDefaultOverlaySettings(screen.getPrimaryDisplay().id);
  }
  return currentSettings;
}

export async function setOverlaySettings(value: unknown): Promise<OverlaySettings> {
  return runSettingsTransaction(async () => {
    const { primaryDisplayId, displayIds } = getDisplayContext();
    const settings = normalizeOverlaySettings(value, primaryDisplayId, displayIds);
    if (!settings) throw new TypeError("Invalid overlay settings");

    await persistOverlaySettings(settings);
    currentSettings = settings;
    return settings;
  });
}

export async function reconcileOverlayTarget(): Promise<OverlaySettings> {
  return runSettingsTransaction(async () => {
    const settings = getOverlaySettings();
    const { primaryDisplayId, displayIds } = getDisplayContext();
    if (displayIds.includes(settings.targetDisplayId)) return settings;

    const reconciledSettings = {
      ...settings,
      targetDisplayId: primaryDisplayId
    };
    await persistOverlaySettings(reconciledSettings);
    currentSettings = reconciledSettings;
    return reconciledSettings;
  });
}
