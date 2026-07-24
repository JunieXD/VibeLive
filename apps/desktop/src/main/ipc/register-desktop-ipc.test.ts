import { beforeEach, describe, expect, it, vi } from "vitest";

const electronMocks = vi.hoisted(() => {
  const ipcHandlers = new Map<string, (...args: unknown[]) => unknown>();
  const ipcListeners = new Map<string, (...args: unknown[]) => unknown>();
  return {
    displayMediaHandler: undefined as
      | ((request: unknown, callback: (streams: unknown) => void) => void)
      | undefined,
    getSources: vi.fn(),
    ipcHandlers,
    ipcListeners,
    permissionCheckHandler: undefined as ((...args: unknown[]) => boolean) | undefined,
    permissionRequestHandler: undefined as
      | ((...args: unknown[]) => void)
      | undefined
  };
});

vi.mock("electron", () => ({
  app: {
    getPath: vi.fn(() => "C:\\advx-test"),
    isPackaged: false,
    once: vi.fn()
  },
  BrowserWindow: {
    getAllWindows: vi.fn(() => [])
  },
  desktopCapturer: {
    getSources: electronMocks.getSources
  },
  ipcMain: {
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      electronMocks.ipcHandlers.set(channel, handler);
    }),
    on: vi.fn((channel: string, listener: (...args: unknown[]) => unknown) => {
      electronMocks.ipcListeners.set(channel, listener);
    })
  },
  safeStorage: {
    decryptString: vi.fn(),
    encryptString: vi.fn(),
    isEncryptionAvailable: vi.fn(() => false)
  },
  screen: {
    getAllDisplays: vi.fn(() => []),
    getPrimaryDisplay: vi.fn(() => ({ id: 1 }))
  },
  session: {
    defaultSession: {
      setDisplayMediaRequestHandler: vi.fn((handler) => {
        electronMocks.displayMediaHandler = handler;
      }),
      setPermissionCheckHandler: vi.fn((handler) => {
        electronMocks.permissionCheckHandler = handler;
      }),
      setPermissionRequestHandler: vi.fn((handler) => {
        electronMocks.permissionRequestHandler = handler;
      })
    }
  },
  systemPreferences: {
    askForMediaAccess: vi.fn(),
    getMediaAccessStatus: vi.fn(() => "granted")
  }
}));

import {
  configureMediaAccess,
  registerDesktopIpc
} from "./register-desktop-ipc";

const source = {
  id: "screen:1:0",
  name: "Screen 1",
  display_id: "1",
  thumbnail: {
    toDataURL: () => "data:image/png;base64,test"
  },
  appIcon: null,
  getMediaSourceId: () => "screen:1:0"
};

const controlWindow = {
  isDestroyed: () => false,
  getMediaSourceId: () => "window:control",
  webContents: {
    id: 7,
    mainFrame: {
      frameTreeNodeId: 77
    },
    send: vi.fn()
  }
};

const backendClient = {
  onStatus: vi.fn(),
  onBarrage: vi.fn(),
  onViewerEvent: vi.fn(),
  onTranscript: vi.fn(),
  notifyVoiceActivity: vi.fn(),
  submitAudioSegment: vi.fn()
};

async function authorizeDisplayCapture(): Promise<void> {
  const selectSource = electronMocks.ipcHandlers.get("desktop:select-source");
  await selectSource?.({ sender: controlWindow.webContents }, source.id);
}

function requestDisplayMedia(
  request: { videoRequested: boolean; audioRequested: boolean; frame: object }
): Promise<unknown> {
  return new Promise((resolve) => {
    electronMocks.displayMediaHandler?.(request, resolve);
  });
}

describe("desktop media access", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    electronMocks.ipcHandlers.clear();
    electronMocks.ipcListeners.clear();
    electronMocks.getSources.mockReset();
    electronMocks.getSources.mockResolvedValue([source]);
    registerDesktopIpc(
      () => controlWindow as never,
      vi.fn(),
      backendClient as never,
      vi.fn()
    );
  });

  it("grants Windows loopback only once to the authorized control frame", async () => {
    configureMediaAccess(() => controlWindow as never, "win32");
    await authorizeDisplayCapture();

    await expect(
      requestDisplayMedia({
        videoRequested: true,
        audioRequested: true,
        frame: controlWindow.webContents.mainFrame
      })
    ).resolves.toEqual({
      video: source,
      audio: "loopback"
    });
    await expect(
      requestDisplayMedia({
        videoRequested: true,
        audioRequested: true,
        frame: controlWindow.webContents.mainFrame
      })
    ).resolves.toEqual({});
  });

  it("rejects an authorized request from a different frame", async () => {
    configureMediaAccess(() => controlWindow as never, "win32");
    await authorizeDisplayCapture();

    await expect(
      requestDisplayMedia({
        videoRequested: true,
        audioRequested: true,
        frame: { frameTreeNodeId: 99 }
      })
    ).resolves.toEqual({});
  });

  it("keeps video capture but omits system audio on non-Windows platforms", async () => {
    configureMediaAccess(() => controlWindow as never, "linux");
    await authorizeDisplayCapture();

    await expect(
      requestDisplayMedia({
        videoRequested: true,
        audioRequested: true,
        frame: controlWindow.webContents.mainFrame
      })
    ).resolves.toEqual({ video: source });
  });

  it("never grants audio without a requested and valid video source", async () => {
    configureMediaAccess(() => controlWindow as never, "win32");
    await authorizeDisplayCapture();
    await expect(
      requestDisplayMedia({
        videoRequested: false,
        audioRequested: true,
        frame: controlWindow.webContents.mainFrame
      })
    ).resolves.toEqual({});

    await authorizeDisplayCapture();
    electronMocks.getSources.mockResolvedValue([]);
    await expect(
      requestDisplayMedia({
        videoRequested: true,
        audioRequested: true,
        frame: controlWindow.webContents.mainFrame
      })
    ).resolves.toEqual({});
  });

  it("reports the host platform system-audio capability", () => {
    expect(electronMocks.ipcHandlers.get("media:get-access-status")?.()).toMatchObject({
      systemAudioSupported: process.platform === "win32"
    });
  });

  it("forwards source-aware audio input and transcripts through the control window", async () => {
    const audio = {
      source: "system_audio" as const,
      inputId: "audio-1",
      capturedAtMs: 10,
      body: new Uint8Array([1])
    };
    await electronMocks.ipcHandlers
      .get("backend:submit-audio")
      ?.({ sender: controlWindow.webContents }, audio);
    electronMocks.ipcListeners
      .get("backend:voice-activity")
      ?.({ sender: controlWindow.webContents }, "system_audio", 20);

    const transcript = {
      source: "system_audio" as const,
      text: "hello",
      final: true,
      startedAtMs: 10,
      endedAtMs: 20,
      utteranceId: "utterance-1",
      revision: 1
    };
    const transcriptListener = backendClient.onTranscript.mock.calls.at(-1)?.[0];
    transcriptListener?.(transcript);

    expect(backendClient.submitAudioSegment).toHaveBeenCalledWith(audio);
    expect(backendClient.notifyVoiceActivity).toHaveBeenCalledWith(
      "system_audio",
      20
    );
    expect(controlWindow.webContents.send).toHaveBeenCalledWith(
      "backend:transcript",
      transcript
    );
  });
});
