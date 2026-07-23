import type { OverlaySettings } from "../shared/contracts";

export function createDefaultOverlaySettings(primaryDisplayId: number): OverlaySettings {
  return {
    targetDisplayId: primaryDisplayId,
    fontSizePx: 22,
    speed: 58,
    opacity: 86,
    density: 6,
    region: {
      topPercent: 5,
      bottomPercent: 75
    },
    clickThrough: true
  };
}

function isNumberInRange(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum;
}

export function normalizeOverlaySettings(
  value: unknown,
  primaryDisplayId: number,
  displayIds: readonly number[]
): OverlaySettings | null {
  if (!value || typeof value !== "object") return null;

  const settings = value as Partial<OverlaySettings>;
  const region = settings.region;
  if (
    !Number.isInteger(settings.targetDisplayId) ||
    !isNumberInRange(settings.fontSizePx, 14, 36) ||
    !isNumberInRange(settings.speed, 20, 100) ||
    !isNumberInRange(settings.opacity, 30, 100) ||
    !isNumberInRange(settings.density, 1, 10) ||
    !region ||
    !isNumberInRange(region.topPercent, 0, 100) ||
    !isNumberInRange(region.bottomPercent, 0, 100) ||
    region.bottomPercent - region.topPercent < 20 ||
    typeof settings.clickThrough !== "boolean"
  ) {
    return null;
  }

  return {
    targetDisplayId: displayIds.includes(settings.targetDisplayId as number)
      ? (settings.targetDisplayId as number)
      : primaryDisplayId,
    fontSizePx: settings.fontSizePx,
    speed: settings.speed,
    opacity: settings.opacity,
    density: settings.density,
    region: {
      topPercent: region.topPercent,
      bottomPercent: region.bottomPercent
    },
    clickThrough: settings.clickThrough
  };
}
