import { beforeEach, describe, expect, it, vi } from "vitest";

const electronMocks = vi.hoisted(() => ({
  setActivationPolicy: vi.fn(),
  showDock: vi.fn()
}));

vi.mock("electron", () => ({
  app: {
    setActivationPolicy: electronMocks.setActivationPolicy,
    dock: { show: electronMocks.showDock }
  }
}));

import { restoreMacApplicationActivation } from "./mac-application-activation";

describe("mac application activation", () => {
  beforeEach(() => {
    electronMocks.setActivationPolicy.mockClear();
    electronMocks.showDock.mockClear();
  });

  it("keeps the macOS application in the regular activation policy", () => {
    restoreMacApplicationActivation();

    if (process.platform === "darwin") {
      expect(electronMocks.setActivationPolicy).toHaveBeenCalledWith("regular");
      expect(electronMocks.showDock).toHaveBeenCalledOnce();
    } else {
      expect(electronMocks.setActivationPolicy).not.toHaveBeenCalled();
      expect(electronMocks.showDock).not.toHaveBeenCalled();
    }
  });
});
