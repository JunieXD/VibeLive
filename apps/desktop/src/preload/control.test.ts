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
  it("subscribes to backend transcripts and removes the exact handler", () => {
    const api = electronMocks.exposeInMainWorld.mock.calls.find(
      ([name]) => name === "advx"
    )?.[1] as ControlApi | undefined;
    const listener = vi.fn();

    const unsubscribe = api?.onBackendTranscript(listener);
    const handler = electronMocks.on.mock.calls.find(
      ([channel]) => channel === "backend:transcript"
    )?.[1];
    const transcript = {
      source: "microphone" as const,
      text: "hello",
      final: true,
      startedAtMs: 10,
      endedAtMs: 20,
      utteranceId: "utterance-1",
      revision: 1
    };
    handler?.({}, transcript);
    unsubscribe?.();

    expect(listener).toHaveBeenCalledWith(transcript);
    expect(electronMocks.removeListener).toHaveBeenCalledWith(
      "backend:transcript",
      handler
    );
  });

});
