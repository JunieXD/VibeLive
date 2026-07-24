import { app, BrowserWindow, screen, shell } from "electron";
import { join } from "node:path";
import type { BarrageEvent } from "../../shared/contracts";
import { overlayIpcEnvelope } from "../../shared/overlay-protocol";
import { loadRenderer } from "./load-renderer";

let floatingChatWindow: BrowserWindow | null = null;
let rendererReady = false;
let clearPending = false;
let pendingMessages: BarrageEvent[] = [];
let allowWindowClose = false;
let hideRequestHandler: (() => void) | null = null;

app.once("before-quit", () => {
  allowWindowClose = true;
});

function defaultWindowBounds(): Electron.Rectangle {
  const { workArea } = screen.getPrimaryDisplay();
  const width = Math.min(420, Math.max(340, workArea.width - 32));
  const height = Math.min(760, Math.max(500, workArea.height - 80));

  return {
    x: workArea.x + workArea.width - width - 24,
    y: workArea.y + Math.max(24, Math.floor((workArea.height - height) / 2)),
    width,
    height
  };
}

function flushPendingMessages(window: BrowserWindow): void {
  if (clearPending) {
    window.webContents.send("overlay:clear", overlayIpcEnvelope(true));
    clearPending = false;
  }
  const queued = pendingMessages;
  pendingMessages = [];
  for (const message of queued) {
    window.webContents.send("overlay:barrage", overlayIpcEnvelope(message));
  }
}

export function createFloatingChatWindow(): BrowserWindow {
  const window = new BrowserWindow({
    ...defaultWindowBounds(),
    title: "ADVX Live 直播互动",
    frame: false,
    transparent: false,
    resizable: true,
    movable: true,
    minimizable: true,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: false,
    show: false,
    minWidth: 340,
    minHeight: 500,
    backgroundColor: "#17191c",
    webPreferences: {
      preload: join(__dirname, "../preload/floating-chat.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });

  window.setAlwaysOnTop(true, "floating");
  window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.on("did-start-navigation", (_event, _url, _inPlace, isMainFrame) => {
    if (isMainFrame) rendererReady = false;
  });
  window.on("close", (event) => {
    if (allowWindowClose) return;
    event.preventDefault();
    hideRequestHandler?.();
  });
  loadRenderer(window, "floating-chat");
  return window;
}

function getFloatingChatWindow(): BrowserWindow {
  if (floatingChatWindow && !floatingChatWindow.isDestroyed()) {
    return floatingChatWindow;
  }

  floatingChatWindow = createFloatingChatWindow();
  floatingChatWindow.on("closed", () => {
    floatingChatWindow = null;
    rendererReady = false;
    clearPending = false;
    pendingMessages = [];
  });
  return floatingChatWindow;
}

export function setFloatingChatHideRequestHandler(
  handler: (() => void) | null
): void {
  hideRequestHandler = handler;
}

export function showFloatingChat(): void {
  getFloatingChatWindow().showInactive();
}

export function hideFloatingChat(): void {
  floatingChatWindow?.hide();
}

export function minimizeFloatingChat(): void {
  if (!floatingChatWindow || floatingChatWindow.isDestroyed()) return;
  floatingChatWindow.minimize();
}

export function clearFloatingChat(): void {
  if (!floatingChatWindow || floatingChatWindow.isDestroyed()) return;
  pendingMessages = [];
  if (!rendererReady) {
    clearPending = true;
    return;
  }
  floatingChatWindow.webContents.send("overlay:clear", overlayIpcEnvelope(true));
}

export function pushFloatingChatMessage(event: BarrageEvent): void {
  const window = getFloatingChatWindow();
  if (!rendererReady) {
    pendingMessages.push(event);
    return;
  }
  window.webContents.send("overlay:barrage", overlayIpcEnvelope(event));
}

export function markFloatingChatRendererReady(webContentsId: number): void {
  if (!isFloatingChatSender(webContentsId) || !floatingChatWindow) return;
  rendererReady = true;
  flushPendingMessages(floatingChatWindow);
}

export function isFloatingChatSender(webContentsId: number): boolean {
  return (
    floatingChatWindow !== null &&
    !floatingChatWindow.isDestroyed() &&
    floatingChatWindow.webContents.id === webContentsId
  );
}
