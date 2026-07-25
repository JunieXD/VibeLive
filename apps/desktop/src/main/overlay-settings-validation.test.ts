import { describe, expect, it } from "vitest";
import {
  createDefaultOverlaySettings,
  normalizeOverlaySettings
} from "./overlay-settings-validation";

describe("overlay settings validation", () => {
  it("returns null for damaged persisted data so the caller can restore defaults", () => {
    expect(normalizeOverlaySettings(null, 1, [1])).toBeNull();
    expect(
      normalizeOverlaySettings({ targetDisplayId: 1, fontSizePx: "large" }, 1, [1])
    ).toBeNull();
  });

  it("falls back to the primary display when the configured display is missing", () => {
    const settings = createDefaultOverlaySettings(99);
    expect(normalizeOverlaySettings(settings, 1, [1, 2])?.targetDisplayId).toBe(1);
  });

  it("accepts density through 100", () => {
    const settings = { ...createDefaultOverlaySettings(1), density: 100 };

    expect(normalizeOverlaySettings(settings, 1, [1])).toEqual(settings);
    expect(normalizeOverlaySettings({ ...settings, density: 101 }, 1, [1])).toBeNull();
  });

  it("migrates legacy settings with the new Bilibili-style text defaults", () => {
    const settings = createDefaultOverlaySettings(1);
    const legacySettings = { ...settings } as Partial<typeof settings>;
    delete legacySettings.fontFamily;
    delete legacySettings.bold;
    delete legacySettings.outlineWidthPx;
    delete legacySettings.displayMode;

    expect(normalizeOverlaySettings(legacySettings, 1, [1])).toEqual(settings);
  });

  it("accepts the floating interaction window as the second display mode", () => {
    const settings = {
      ...createDefaultOverlaySettings(1),
      displayMode: "floating" as const
    };

    expect(normalizeOverlaySettings(settings, 1, [1])).toEqual(settings);
  });
});
