import { describe, expect, it } from "vitest";
import {
  createDefaultOverlaySettings,
  normalizeOverlaySettings
} from "./overlay-settings-validation";

describe("overlay settings validation", () => {
  it("creates the requested defaults for the primary display", () => {
    expect(createDefaultOverlaySettings(42)).toEqual({
      targetDisplayId: 42,
      fontSizePx: 22,
      speed: 58,
      opacity: 86,
      density: 6,
      region: { topPercent: 5, bottomPercent: 75 },
      clickThrough: true
    });
  });

  it("rejects out-of-range values and regions shorter than 20 percent", () => {
    const defaults = createDefaultOverlaySettings(1);
    for (const invalid of [
      { ...defaults, fontSizePx: 13 },
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
});
