import { BrowserWindow, shell } from "electron";
import { join } from "node:path";
import type { ColorTheme } from "../../shared/contracts";
import { loadRenderer } from "./load-renderer";

const CONTROL_WINDOW_THEMES: Record<
  ColorTheme,
  {
    backgroundColor: string;
    titleBarOverlay: { color: string; symbolColor: string; height: number };
  }
> = {
  dark: {
    backgroundColor: "#0e0f12",
    titleBarOverlay: {
      color: "#0e0f12",
      symbolColor: "#e8eaf0",
      height: 32
    }
  },
  light: {
    backgroundColor: "#f6f7f9",
    titleBarOverlay: {
      color: "#f6f7f9",
      symbolColor: "#17191d",
      height: 32
    }
  }
};

export function applyControlWindowTheme(window: BrowserWindow, theme: ColorTheme): void {
  const colors = CONTROL_WINDOW_THEMES[theme];
  window.setBackgroundColor(colors.backgroundColor);
  if (process.platform !== "darwin") {
    window.setTitleBarOverlay(colors.titleBarOverlay);
  }
}

export function createControlWindow(): BrowserWindow {
  const initialTheme = CONTROL_WINDOW_THEMES.dark;
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1120,
    minHeight: 720,
    show: false,
    title: "ADVX Live",
    autoHideMenuBar: true,
    titleBarStyle: "hidden",
    titleBarOverlay: initialTheme.titleBarOverlay,
    backgroundColor: initialTheme.backgroundColor,
    webPreferences: {
      preload: join(__dirname, "../preload/control.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });

  window.removeMenu();
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  loadRenderer(window, "control");
  return window;
}
