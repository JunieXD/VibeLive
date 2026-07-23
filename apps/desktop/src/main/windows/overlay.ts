import { BrowserWindow, Rectangle, screen, shell } from "electron";
import { join } from "node:path";
import type { BarrageEvent, OverlaySettings } from "../../shared/contracts";
import { getOverlaySettings } from "../overlay-settings";
import { loadRenderer } from "./load-renderer";

let overlayWindow: BrowserWindow | null = null;

export function createOverlayWindow(bounds: Rectangle, clickThrough = true): BrowserWindow {
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

  window.setAlwaysOnTop(true, clickThrough ? "screen-saver" : "pop-up-menu");
  window.setBounds(bounds);
  window.setIgnoreMouseEvents(clickThrough, { forward: true });
  window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.once("did-finish-load", () => {
    window.webContents.send("overlay:settings-changed", getOverlaySettings());
  });
  loadRenderer(window, "overlay");
  return window;
}

function getTargetBounds(settings: OverlaySettings): Rectangle {
  return (
    screen.getAllDisplays().find((display) => display.id === settings.targetDisplayId) ??
    screen.getPrimaryDisplay()
  ).bounds;
}

function getOverlayWindow(): BrowserWindow {
  if (overlayWindow && !overlayWindow.isDestroyed()) return overlayWindow;

  const settings = getOverlaySettings();
  overlayWindow = createOverlayWindow(getTargetBounds(settings), settings.clickThrough);
  overlayWindow.on("closed", () => {
    overlayWindow = null;
  });
  return overlayWindow;
}

function sendWhenReady(
  window: BrowserWindow,
  channel: string,
  payload?: BarrageEvent | OverlaySettings
): void {
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
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  sendWhenReady(overlayWindow, "overlay:clear");
}

export function pushBarrage(event: BarrageEvent): void {
  const window = getOverlayWindow();
  if (!window.isVisible()) window.showInactive();
  sendWhenReady(window, "overlay:barrage", event);
}

export function applyOverlaySettings(settings: OverlaySettings): void {
  if (!overlayWindow || overlayWindow.isDestroyed()) return;

  overlayWindow.setAlwaysOnTop(
    true,
    settings.clickThrough ? "screen-saver" : "pop-up-menu"
  );
  overlayWindow.setBounds(getTargetBounds(settings));
  overlayWindow.setIgnoreMouseEvents(settings.clickThrough, { forward: true });
  sendWhenReady(overlayWindow, "overlay:settings-changed", settings);
}
