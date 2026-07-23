import { describe, expect, it, vi } from 'vitest'
import {
  COMPRESSION_PROFILES,
  DEFAULT_VISUAL_SETTINGS,
  VISUAL_SETTINGS_STORAGE_KEY,
  cameraPreviewTransform,
  createWaitingVisualBatchSink,
  deliverAndReleaseVisualBatch,
  encodeJpegWithinTarget,
  getContainRectangle,
  getPipRectangle,
  loadVisualSettings,
  parseVisualSettings,
  releaseVisualFrames,
  resolveVisualMode,
  saveVisualSettings,
  selectVisualBatchFrames,
  type VisualFrame
} from './visual'

function createFrame(capturedAt: number): VisualFrame {
  const blob = new Blob([new Uint8Array([capturedAt])], { type: 'image/jpeg' })
  return {
    frameId: String(capturedAt),
    capturedAt,
    width: 1440,
    height: 810,
    mode: 'pip',
    bytes: blob.size,
    overTarget: false,
    blob
  }
}

describe('visual composition helpers', () => {
  it('places medium picture-in-picture in each requested corner', () => {
    expect(getPipRectangle(1600, 900, 'top-left', 'medium')).toEqual({
      x: 23,
      y: 23,
      width: 448,
      height: 252
    })
    expect(getPipRectangle(1600, 900, 'bottom-right', 'medium')).toEqual({
      x: 1129,
      y: 625,
      width: 448,
      height: 252
    })
  })

  it('contains a camera frame without cropping and mirrors only when requested', () => {
    expect(getContainRectangle(1920, 1080, { x: 10, y: 20, width: 400, height: 400 })).toEqual({
      x: 10,
      y: 107.5,
      width: 400,
      height: 225
    })
    expect(cameraPreviewTransform(false)).toBe('none')
    expect(cameraPreviewTransform(true)).toBe('scaleX(-1)')
  })

  it('downgrades picture-in-picture to the remaining source', () => {
    expect(resolveVisualMode('pip', true, true)).toBe('pip')
    expect(resolveVisualMode('pip', true, false)).toBe('screen')
    expect(resolveVisualMode('pip', false, true)).toBe('camera')
    expect(resolveVisualMode('pip', false, false)).toBeNull()
  })
})

describe('visual compression', () => {
  it.each([
    ['economy', 960, 120 * 1024],
    ['balanced', 1440, 250 * 1024],
    ['clear', 1920, 500 * 1024]
  ] as const)('uses the %s profile limits', async (preset, expectedLongEdge, targetBytes) => {
    const encode = vi.fn(async (width: number, height: number, quality: number) => {
      const size = quality <= 0.74 ? targetBytes - 1 : targetBytes + 1
      return new Blob([new Uint8Array(size)], { type: 'image/jpeg' })
    })

    const result = await encodeJpegWithinTarget(
      3840,
      2160,
      COMPRESSION_PROFILES[preset],
      encode
    )

    expect(Math.max(result.width, result.height)).toBe(expectedLongEdge)
    expect(result.blob.size).toBeLessThanOrEqual(targetBytes)
    expect(result.overTarget).toBe(false)
  })

  it('stops shrinking at the 720px readable floor and reports an oversized result', async () => {
    const visitedLongEdges: number[] = []
    const encode = vi.fn(async (width: number, height: number) => {
      visitedLongEdges.push(Math.max(width, height))
      return new Blob([new Uint8Array(300 * 1024)], { type: 'image/jpeg' })
    })
    const result = await encodeJpegWithinTarget(
      3840,
      2160,
      COMPRESSION_PROFILES.balanced,
      encode
    )

    expect(Math.min(...visitedLongEdges)).toBe(720)
    expect(result.width).toBe(720)
    expect(result.overTarget).toBe(true)
    expect(encode.mock.calls.length).toBeLessThanOrEqual(12)
  })
})

describe('visual batching and settings', () => {
  it('selects the earliest and latest sampled frames without deduplicating', () => {
    const frames = [createFrame(300), createFrame(100), createFrame(200)]
    expect(selectVisualBatchFrames(frames).map((frame) => frame.capturedAt)).toEqual([100, 300])
    const duplicates = [createFrame(100), createFrame(100)]
    expect(selectVisualBatchFrames(duplicates)).toHaveLength(2)
  })

  it('releases selected and discarded Blob references', async () => {
    const selected = [createFrame(100), createFrame(300)]
    const discarded = [createFrame(200)]
    const consume = vi.fn(async () => 'accepted' as const)
    const controller = new AbortController()

    releaseVisualFrames(discarded)
    const result = await deliverAndReleaseVisualBatch(
      { consume },
      { batchId: 'batch-1', createdAt: 400, frames: selected },
      controller.signal
    )

    expect(result).toBe('accepted')
    expect(consume).toHaveBeenCalledOnce()
    expect(selected.every((frame) => frame.blob === null)).toBe(true)
    expect(discarded[0].blob).toBeNull()
  })

  it('keeps the placeholder sink waiting and cancels slow delivery while releasing Blobs', async () => {
    const waitingFrames = [createFrame(100)]
    const waitingResult = await deliverAndReleaseVisualBatch(
      createWaitingVisualBatchSink(),
      { batchId: 'batch-waiting', createdAt: 200, frames: waitingFrames },
      new AbortController().signal
    )
    expect(waitingResult).toBe('waiting-backend')
    expect(waitingFrames[0].blob).toBeNull()

    const slowFrames = [createFrame(300)]
    const controller = new AbortController()
    const delivery = deliverAndReleaseVisualBatch(
      {
        consume: async (_batch, signal) =>
          new Promise((_resolve, reject) => {
            signal.addEventListener(
              'abort',
              () => reject(new DOMException('Visual batch delivery was aborted.', 'AbortError')),
              { once: true }
            )
          })
      },
      { batchId: 'batch-slow', createdAt: 400, frames: slowFrames },
      controller.signal
    )
    controller.abort()

    await expect(delivery).rejects.toMatchObject({ name: 'AbortError' })
    expect(slowFrames[0].blob).toBeNull()
  })

  it('restores only versioned valid settings and safely falls back', () => {
    const values = new Map<string, string>()
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value)
    }
    const settings = {
      ...DEFAULT_VISUAL_SETTINGS,
      mode: 'pip' as const,
      mirrorCamera: true,
      compressionPreset: 'clear' as const
    }

    expect(saveVisualSettings(storage, settings)).toBe(true)
    expect(loadVisualSettings(storage)).toEqual(settings)
    values.set(VISUAL_SETTINGS_STORAGE_KEY, JSON.stringify({ version: 0, settings }))
    expect(loadVisualSettings(storage)).toEqual(DEFAULT_VISUAL_SETTINGS)
    expect(parseVisualSettings({ version: 1, settings: { mode: 'invalid' } })).toEqual(
      DEFAULT_VISUAL_SETTINGS
    )
  })
})
