import type {
  BarrageDisplayMode,
  OverlayFontFamily,
  OverlaySettings
} from "../shared/contracts";

const OVERLAY_FONT_FAMILIES: readonly OverlayFontFamily[] = [
  "bilibili",
  "yahei",
  "system"
];
const BARRAGE_DISPLAY_MODES: readonly BarrageDisplayMode[] = [
  "overlay",
  "floating"
];

export function createDefaultOverlaySettings(primaryDisplayId: number): OverlaySettings {
  return {
    displayModes: ["overlay"],
    targetDisplayId: primaryDisplayId,
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
}

function isNumberInRange(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum;
}

function isOverlayFontFamily(value: unknown): value is OverlayFontFamily {
  return OVERLAY_FONT_FAMILIES.includes(value as OverlayFontFamily);
}

function isBarrageDisplayMode(value: unknown): value is BarrageDisplayMode {
  return BARRAGE_DISPLAY_MODES.includes(value as BarrageDisplayMode);
}

function normalizeBarrageDisplayModes(value: unknown): BarrageDisplayMode[] | null {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    !value.every(isBarrageDisplayMode) ||
    new Set(value).size !== value.length
  ) {
    return null;
  }

  return BARRAGE_DISPLAY_MODES.filter((mode) => value.includes(mode));
}

export function normalizeOverlaySettings(
  value: unknown,
  primaryDisplayId: number,
  displayIds: readonly number[]
): OverlaySettings | null {
  if (!value || typeof value !== "object") return null;

  const settings = value as Partial<OverlaySettings> & { displayMode?: unknown };
  const defaults = createDefaultOverlaySettings(primaryDisplayId);
  const region = settings.region;
  const displayModes = normalizeBarrageDisplayModes(
    settings.displayModes ?? [settings.displayMode ?? defaults.displayModes[0]]
  );
  const fontFamily = settings.fontFamily ?? defaults.fontFamily;
  const bold = settings.bold ?? defaults.bold;
  const outlineWidthPx = settings.outlineWidthPx ?? defaults.outlineWidthPx;
  if (
    !displayModes ||
    !Number.isInteger(settings.targetDisplayId) ||
    !isNumberInRange(settings.fontSizePx, 14, 36) ||
    !isOverlayFontFamily(fontFamily) ||
    typeof bold !== "boolean" ||
    !isNumberInRange(outlineWidthPx, 0, 3) ||
    !isNumberInRange(settings.speed, 20, 100) ||
    !isNumberInRange(settings.opacity, 30, 100) ||
    !isNumberInRange(settings.density, 1, 100) ||
    !region ||
    !isNumberInRange(region.topPercent, 0, 100) ||
    !isNumberInRange(region.bottomPercent, 0, 100) ||
    region.bottomPercent - region.topPercent < 20
  ) {
    return null;
  }

  return {
    displayModes,
    targetDisplayId: displayIds.includes(settings.targetDisplayId as number)
      ? (settings.targetDisplayId as number)
      : primaryDisplayId,
    fontSizePx: settings.fontSizePx,
    fontFamily,
    bold,
    outlineWidthPx,
    speed: settings.speed,
    opacity: settings.opacity,
    density: settings.density,
    region: {
      topPercent: region.topPercent,
      bottomPercent: region.bottomPercent
    }
  };
}
