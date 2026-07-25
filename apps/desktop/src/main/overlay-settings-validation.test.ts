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
    delete legacySettings.displayModes;

    expect(normalizeOverlaySettings(legacySettings, 1, [1])).toEqual(settings);
  });

  it("migrates a legacy single display mode", () => {
    const settings = {
      ...createDefaultOverlaySettings(1),
      displayMode: "floating" as const
    };
    delete (settings as Partial<typeof settings>).displayModes;

    expect(normalizeOverlaySettings(settings, 1, [1])).toEqual({
      ...createDefaultOverlaySettings(1),
      displayModes: ["floating"]
    });
  });

  it("accepts both display outputs at once", () => {
    const settings = {
      ...createDefaultOverlaySettings(1),
      displayModes: ["overlay", "floating"] as const
    };

    expect(normalizeOverlaySettings(settings, 1, [1])).toEqual(settings);
  });

  it("rejects an empty or duplicated display-output selection", () => {
    const settings = createDefaultOverlaySettings(1);

    expect(
      normalizeOverlaySettings({ ...settings, displayModes: [] }, 1, [1])
    ).toBeNull();
    expect(
      normalizeOverlaySettings({ ...settings, displayModes: ["overlay", "overlay"] }, 1, [1])
    ).toBeNull();
  });
});
