import { describe, expect, it } from 'vitest'
import {
  OVERLAY_PROTOCOL_VERSION,
  overlayIpcEnvelope,
  readOverlayIpcEnvelope
} from './overlay-protocol'

describe('overlay IPC protocol', () => {
  it('accepts v2 envelopes and rejects mismatched versions', () => {
    const payload = { barrageId: 'barrage-1' }

    expect(
      readOverlayIpcEnvelope(
        overlayIpcEnvelope(payload),
        OVERLAY_PROTOCOL_VERSION
      )
    ).toEqual(payload)
    expect(
      readOverlayIpcEnvelope(
        { protocolVersion: 1, payload },
        OVERLAY_PROTOCOL_VERSION
      )
    ).toBeNull()
    expect(
      readOverlayIpcEnvelope(
        { protocolVersion: 999, payload },
        OVERLAY_PROTOCOL_VERSION
      )
    ).toBeNull()
    expect(readOverlayIpcEnvelope(overlayIpcEnvelope(true))).toBe(true)
  })
})
