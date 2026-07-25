import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BarrageEvent, OverlaySettings } from "../../shared/contracts";

const outputMocks = vi.hoisted(() => ({
  getOverlaySettings: vi.fn(),
  applyOverlaySettings: vi.fn(),
  clearOverlay: vi.fn(),
  hideOverlay: vi.fn(),
  pushBarrage: vi.fn(),
  showOverlay: vi.fn(),
  clearFloatingChat: vi.fn(),
  hideFloatingChat: vi.fn(),
  pushFloatingChatMessage: vi.fn(),
  setFloatingChatHideRequestHandler: vi.fn(),
  showFloatingChat: vi.fn()
}));

vi.mock("../overlay-settings", () => ({
  getOverlaySettings: outputMocks.getOverlaySettings
}));

vi.mock("./overlay", () => ({
  applyOverlaySettings: outputMocks.applyOverlaySettings,
  clearOverlay: outputMocks.clearOverlay,
  hideOverlay: outputMocks.hideOverlay,
  pushBarrage: outputMocks.pushBarrage,
  showOverlay: outputMocks.showOverlay
}));

vi.mock("./floating-chat", () => ({
  clearFloatingChat: outputMocks.clearFloatingChat,
  hideFloatingChat: outputMocks.hideFloatingChat,
  pushFloatingChatMessage: outputMocks.pushFloatingChatMessage,
  setFloatingChatHideRequestHandler: outputMocks.setFloatingChatHideRequestHandler,
  showFloatingChat: outputMocks.showFloatingChat
}));

import {
  hideBarrageOutputs,
  pushBarrageToOutputs,
  showBarrageOutputs
} from "./barrage-outputs";

const settings: OverlaySettings = {
  displayModes: ["overlay", "floating"],
  targetDisplayId: 1,
  fontSizePx: 25,
  fontFamily: "bilibili",
  bold: true,
  outlineWidthPx: 1,
  speed: 75,
  opacity: 80,
  density: 6,
  region: {
    topPercent: 0,
    bottomPercent: 50
  }
};

const event: BarrageEvent = {
  barrageId: "barrage-1",
  audienceId: "audience-1",
  text: "同时显示",
  createdAt: 1
};

describe("barrage outputs", () => {
  beforeEach(() => {
    hideBarrageOutputs();
    vi.clearAllMocks();
    outputMocks.getOverlaySettings.mockReturnValue(settings);
  });

  afterEach(() => {
    hideBarrageOutputs();
  });

  it("shows and delivers each barrage to every enabled output", () => {
    expect(showBarrageOutputs()).toBe(true);
    expect(outputMocks.showOverlay).toHaveBeenCalledOnce();
    expect(outputMocks.showFloatingChat).toHaveBeenCalledOnce();

    expect(pushBarrageToOutputs(event)).toBe(true);
    expect(outputMocks.pushBarrage).toHaveBeenCalledWith(event);
    expect(outputMocks.pushFloatingChatMessage).toHaveBeenCalledWith(event);
  });
});
