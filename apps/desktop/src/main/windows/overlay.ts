import { BrowserWindow, Rectangle, screen, shell } from "electron";
import { join } from "node:path";
import type { BarrageEvent } from "../../shared/contracts";
import { loadRenderer } from "./load-renderer";

let overlayWindow: BrowserWindow | null = null;

export function createOverlayWindow(bounds: Rectangle): BrowserWindow {
  const window = new BrowserWindow({
    ...bounds,
    transparent: true,
    frame: false,
    focusable: false,
    resizable: false,
    movable: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: join(__dirname, "../preload/overlay.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });

  window.setAlwaysOnTop(true, "screen-saver");
  window.setIgnoreMouseEvents(true, { forward: true });
  window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  loadRenderer(window, "overlay");
  return window;
}

function getOverlayWindow(): BrowserWindow {
  if (overlayWindow && !overlayWindow.isDestroyed()) return overlayWindow;

  overlayWindow = createOverlayWindow(screen.getPrimaryDisplay().bounds);
  overlayWindow.on("closed", () => {
    overlayWindow = null;
  });
  return overlayWindow;
}

function sendWhenReady(channel: string, payload?: BarrageEvent): void {
  const window = getOverlayWindow();
  const send = (): void => window.webContents.send(channel, payload);

  if (window.webContents.isLoadingMainFrame()) {
    window.webContents.once("did-finish-load", send);
  } else {
    send();
  }
}

export function showOverlay(): void {
  getOverlayWindow().showInactive();
}

export function hideOverlay(): void {
  overlayWindow?.hide();
}

export function clearOverlay(): void {
  sendWhenReady("overlay:clear");
}

export function pushBarrage(event: BarrageEvent): void {
  const window = getOverlayWindow();
  if (!window.isVisible()) window.showInactive();
  sendWhenReady("overlay:barrage", event);
}
