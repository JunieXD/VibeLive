import { app } from "electron";

export function restoreMacApplicationActivation(): void {
  if (process.platform !== "darwin") return;
  app.setActivationPolicy("regular");
  app.dock.show();
}
