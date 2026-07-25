import type { BarrageEvent, OverlaySettings } from "../../shared/contracts";
import { getOverlaySettings } from "../overlay-settings";
import {
  applyOverlaySettings,
  clearOverlay,
  hideOverlay,
  pushBarrage,
  showOverlay
} from "./overlay";
import {
  clearFloatingChat,
  hideFloatingChat,
  pushFloatingChatMessage,
  setFloatingChatHideRequestHandler,
  showFloatingChat
} from "./floating-chat";

let outputsVisible = false;
let visibilityListener: ((visible: boolean) => void) | null = null;

function notifyVisibility(visible: boolean): void {
  visibilityListener?.(visible);
}

function showConfiguredOutputs(settings: OverlaySettings): void {
  if (settings.displayModes.includes("overlay")) showOverlay();
  else hideOverlay();

  if (settings.displayModes.includes("floating")) showFloatingChat();
  else hideFloatingChat();
}

setFloatingChatHideRequestHandler(() => {
  hideBarrageOutputs();
});

export function setBarrageOutputVisibilityListener(
  listener: ((visible: boolean) => void) | null
): void {
  visibilityListener = listener;
}

export function showBarrageOutputs(): boolean {
  const settings = getOverlaySettings();
  showConfiguredOutputs(settings);
  outputsVisible = true;
  notifyVisibility(true);
  return true;
}

export function hideBarrageOutputs(): void {
  outputsVisible = false;
  hideOverlay();
  hideFloatingChat();
  notifyVisibility(false);
}

export function clearBarrageOutputs(): void {
  clearOverlay();
  clearFloatingChat();
}

export function pushBarrageToOutputs(event: BarrageEvent): boolean {
  if (!outputsVisible) return false;

  const settings = getOverlaySettings();
  if (settings.displayModes.includes("overlay")) pushBarrage(event);
  if (settings.displayModes.includes("floating")) pushFloatingChatMessage(event);

  return true;
}

export function applyBarrageOutputSettings(settings: OverlaySettings): void {
  applyOverlaySettings(settings);
  if (outputsVisible) showConfiguredOutputs(settings);
}
