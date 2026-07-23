import { BrowserWindow } from "electron";
import { join } from "node:path";

export function loadRenderer(window: BrowserWindow, entry: string): void {
  const developmentUrl = process.env.ELECTRON_RENDERER_URL;

  if (developmentUrl) {
    void window.loadURL(`${developmentUrl}/${entry}/index.html`);
    return;
  }

  void window.loadFile(join(__dirname, `../renderer/${entry}/index.html`));
}
