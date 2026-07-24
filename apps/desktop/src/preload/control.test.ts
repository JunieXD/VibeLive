import { describe, expect, it, vi } from "vitest";
import type { ControlApi } from "../shared/contracts";

const electronMocks = vi.hoisted(() => ({
  exposeInMainWorld: vi.fn(),
  invoke: vi.fn(),
  on: vi.fn(),
  removeListener: vi.fn(),
  send: vi.fn()
}));

vi.mock("electron", () => ({
  contextBridge: {
    exposeInMainWorld: electronMocks.exposeInMainWorld
  },
  ipcRenderer: {
    invoke: electronMocks.invoke,
    on: electronMocks.on,
    removeListener: electronMocks.removeListener,
    send: electronMocks.send
  }
}));

import "./control";

describe("control preload", () => {
  it("forwards voice activity through Electron IPC", () => {
    const api = electronMocks.exposeInMainWorld.mock.calls.find(
      ([name]) => name === "advx"
    )?.[1] as ControlApi | undefined;

    expect(api).toBeDefined();
    expect(() => api?.notifyVoiceActivity(1234)).not.toThrow();
    expect(electronMocks.send).toHaveBeenCalledWith("backend:voice-activity", 1234);
  });
});
