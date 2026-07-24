export const OVERLAY_PROTOCOL_VERSION = 2 as const

export type OverlayIpcEnvelope<T> = {
  protocolVersion: typeof OVERLAY_PROTOCOL_VERSION
  payload: T
}

export function overlayIpcEnvelope<T>(payload: T): OverlayIpcEnvelope<T> {
  return {
    protocolVersion: OVERLAY_PROTOCOL_VERSION,
    payload
  }
}

export function readOverlayIpcEnvelope<T>(
  value: unknown,
  supportedVersion = OVERLAY_PROTOCOL_VERSION
): T | null {
  if (
    typeof value !== 'object' ||
    value === null ||
    !('protocolVersion' in value) ||
    value.protocolVersion !== supportedVersion ||
    !('payload' in value)
  ) {
    return null
  }
  return value.payload as T
}
