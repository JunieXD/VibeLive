import { describe, expect, it } from "vitest";
import {
  createDefaultOverlaySettings,
  normalizeOverlaySettings
} from "./overlay-settings-validation";

describe("overlay settings validation", () => {
  it("creates the requested defaults for the primary display", () => {
    expect(createDefaultOverlaySettings(42)).toEqual({
      targetDisplayId: 42,
      fontSizePx: 25,
      fontFamily: "bilibili",
      bold: true,
      outlineWidthPx: 1,
      speed: 75,
      opacity: 80,
      density: 6,
      region: { topPercent: 0, bottomPercent: 50 },
      clickThrough: true
    });
  });

  it("rejects out-of-range values and regions shorter than 20 percent", () => {
    const defaults = createDefaultOverlaySettings(1);
    for (const invalid of [
      { ...defaults, fontSizePx: 13 },
      { ...defaults, fontFamily: "comic-sans" },
      { ...defaults, bold: "yes" },
      { ...defaults, outlineWidthPx: 3.5 },
      { ...defaults, speed: 101 },
      { ...defaults, opacity: 29 },
      { ...defaults, density: 11 },
      { ...defaults, region: { topPercent: -1, bottomPercent: 75 } },
      { ...defaults, region: { topPercent: 40, bottomPercent: 59 } }
    ]) {
      expect(normalizeOverlaySettings(invalid, 1, [1])).toBeNull();
    }
  });

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

  it("migrates legacy settings with the new Bilibili-style text defaults", () => {
    const settings = createDefaultOverlaySettings(1);
    const legacySettings = { ...settings } as Partial<typeof settings>;
    delete legacySettings.fontFamily;
    delete legacySettings.bold;
    delete legacySettings.outlineWidthPx;

    expect(normalizeOverlaySettings(legacySettings, 1, [1])).toEqual(settings);
  });
});
