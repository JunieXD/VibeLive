import { describe, expect, it, vi } from "vitest";
import type { OverlayApi, OverlaySettings } from "../shared/contracts";

const electronMocks = vi.hoisted(() => ({
  exposeInMainWorld: vi.fn(),
  on: vi.fn(),
  removeListener: vi.fn()
}));

vi.mock("electron", () => ({
  contextBridge: {
    exposeInMainWorld: electronMocks.exposeInMainWorld
  },
  ipcRenderer: {
    on: electronMocks.on,
    removeListener: electronMocks.removeListener
  }
}));

import "./overlay";

const savedSettings: OverlaySettings = {
  displayModes: ["overlay"],
  targetDisplayId: 1,
  fontSizePx: 30,
  fontFamily: "system",
  bold: false,
  outlineWidthPx: 2,
  speed: 100,
  opacity: 55,
  density: 3,
  region: {
    topPercent: 20,
    bottomPercent: 60
  }
};

describe("overlay preload", () => {
  it("replays settings received before the renderer subscribes", () => {
    const api = electronMocks.exposeInMainWorld.mock.calls.find(
      ([name]) => name === "advxOverlay"
    )?.[1] as OverlayApi | undefined;
    const settingsHandler = electronMocks.on.mock.calls.find(
      ([channel]) => channel === "overlay:settings-changed"
    )?.[1] as
      | ((event: Electron.IpcRendererEvent, message: unknown) => void)
      | undefined;

    expect(api).toBeDefined();
    expect(settingsHandler).toBeDefined();

    settingsHandler?.({} as Electron.IpcRendererEvent, {
      protocolVersion: 2,
      payload: savedSettings
    });

    const listener = vi.fn();
    const unsubscribe = api?.onSettingsChanged(listener);

    expect(listener).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledWith(savedSettings);

    unsubscribe?.();
  });
});
