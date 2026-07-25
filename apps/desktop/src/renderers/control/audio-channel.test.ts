import { describe, expect, it } from 'vitest'
import {
  appendSystemAudioBuffer,
  clearSystemAudioBuffer,
  detectMicrophoneSpeech,
  createAudioChannelState,
  markSystemAudioSubmitted,
  observeSystemAudioChunk,
  pendingStandaloneSystemAudioSnapshot,
  pendingSystemAudioSnapshot,
  releaseFailedLoopbackCapture,
  resetAudioSegment,
  shouldReleaseLoopbackVideo,
  updateAudioTransportError
} from './audio-channel'

describe('audio channel state', () => {
  it('keeps microphone and system audio queues and buffers independent', () => {
    const microphone = createAudioChannelState('microphone')
    const systemAudio = createAudioChannelState('system_audio')
    microphone.chunks.push(new Float32Array([0.5]))
    microphone.sampleCount = 1
    microphone.level = 42
    microphone.error = 'microphone-only'

    expect(systemAudio.chunks).toHaveLength(0)
    expect(systemAudio.sampleCount).toBe(0)
    expect(systemAudio.level).toBe(0)
    expect(systemAudio.error).toBeNull()

    resetAudioSegment(microphone)
    expect(microphone.chunks).toHaveLength(0)
    expect(systemAudio.source).toBe('system_audio')
  })

  it('uses the microphone VAD when available and keeps the RMS gate as a fallback', () => {
    const channel = createAudioChannelState('microphone')
    const calls: Array<[number, number]> = []
    channel.voiceActivityDetector = {
      detect: (samples, sampleRate) => {
        calls.push([samples.length, sampleRate])
        return false
      },
      reset: () => undefined,
      dispose: () => undefined
    }

    expect(detectMicrophoneSpeech(channel, new Float32Array(320), 16_000, true)).toBe(false)
    expect(calls).toEqual([[320, 16_000]])

    channel.voiceActivityDetector = {
      detect: () => null,
      reset: () => undefined,
      dispose: () => undefined
    }
    expect(detectMicrophoneSpeech(channel, new Float32Array(320), 16_000, true)).toBe(true)
  })

  it('falls back to the RMS gate when a microphone VAD fails', () => {
    const channel = createAudioChannelState('microphone')
    let disposed = false
    channel.voiceActivityDetector = {
      detect: () => {
        throw new Error('wasm unavailable')
      },
      reset: () => undefined,
      dispose: () => { disposed = true }
    }

    expect(detectMicrophoneSpeech(channel, new Float32Array(320), 16_000, true)).toBe(true)
    expect(channel.voiceActivityDetector).toBeNull()
    expect(disposed).toBe(true)
  })

  it('releases only the hidden camera-only loopback video', () => {
    expect(shouldReleaseLoopbackVideo('camera', true)).toBe(true)
    expect(shouldReleaseLoopbackVideo('screen', true)).toBe(false)
    expect(shouldReleaseLoopbackVideo('pip', true)).toBe(false)
    expect(shouldReleaseLoopbackVideo('camera', false)).toBe(false)
  })

  it('releases a camera-only display capture when loopback has no audio track', () => {
    const noAudioDisplay = {
      getAudioTracks: () => []
    } as unknown as MediaStream
    let stopped = false

    expect(
      releaseFailedLoopbackCapture(
        'camera',
        noAudioDisplay,
        noAudioDisplay,
        () => { stopped = true }
      )
    ).toBe(true)
    expect(stopped).toBe(true)
  })

  it('stops failed loopback audio while retaining screen and pip video', () => {
    let audioStops = 0
    let captureStops = 0
    const failedLoopback = {
      getAudioTracks: () => [
        { stop: () => { audioStops += 1 } }
      ]
    } as unknown as MediaStream

    expect(
      releaseFailedLoopbackCapture(
        'screen',
        failedLoopback,
        failedLoopback,
        () => { captureStops += 1 }
      )
    ).toBe(false)
    expect(
      releaseFailedLoopbackCapture(
        'pip',
        failedLoopback,
        failedLoopback,
        () => { captureStops += 1 }
      )
    ).toBe(false)
    expect(audioStops).toBe(2)
    expect(captureStops).toBe(0)
  })

  it('keeps source-specific transport failures independent and observable', () => {
    const microphone = createAudioChannelState('microphone')
    const systemAudio = createAudioChannelState('system_audio')
    let microphoneError: string | null = null
    let systemAudioError: string | null = null

    updateAudioTransportError(
      systemAudio,
      new Error('system upload rejected'),
      (error) => { systemAudioError = error }
    )

    expect(systemAudioError).toBe('system upload rejected')
    expect(systemAudio.error).toBe('system upload rejected')
    expect(microphoneError).toBeNull()
    expect(microphone.error).toBeNull()

    updateAudioTransportError(systemAudio, null, (error) => { systemAudioError = error })
    updateAudioTransportError(microphone, null, (error) => { microphoneError = error })
    expect(systemAudioError).toBeNull()
    expect(microphoneError).toBeNull()
  })

  it('keeps only the most recent minute of system audio', () => {
    const systemAudio = createAudioChannelState('system_audio')
    for (let end = 10_000; end <= 70_000; end += 10_000) {
      appendSystemAudioBuffer(systemAudio, new Float32Array(10_000), 1_000, end)
    }

    const snapshot = pendingSystemAudioSnapshot(systemAudio, 70_000, 0)

    expect(systemAudio.bufferedChunks[0]?.startedAtMs).toBe(10_000)
    expect(snapshot?.capturedAtMs).toBe(40_000)
    expect(snapshot?.endedAtMs).toBe(70_000)
    expect(snapshot?.chunks.reduce((total, chunk) => total + chunk.length, 0)).toBe(30_000)
  })

  it('submits only system audio that was not part of the previous microphone turn', () => {
    const systemAudio = createAudioChannelState('system_audio')
    appendSystemAudioBuffer(systemAudio, new Float32Array(10_000), 1_000, 10_000)
    appendSystemAudioBuffer(systemAudio, new Float32Array(10_000), 1_000, 20_000)

    const first = pendingSystemAudioSnapshot(systemAudio, 20_000, 10_000)
    markSystemAudioSubmitted(systemAudio, first?.endedAtMs ?? 0)
    appendSystemAudioBuffer(systemAudio, new Float32Array(10_000), 1_000, 30_000)
    const second = pendingSystemAudioSnapshot(systemAudio, 30_000, 25_000)

    expect(first?.capturedAtMs).toBe(0)
    expect(second?.capturedAtMs).toBe(20_000)
    expect(second?.chunks.reduce((total, chunk) => total + chunk.length, 0)).toBe(10_000)

    clearSystemAudioBuffer(systemAudio)
    expect(pendingSystemAudioSnapshot(systemAudio, 30_000, 25_000)).toBeNull()
  })

  it('bounds a turn snapshot by the last submission, microphone pre-roll and 30 seconds', () => {
    const systemAudio = createAudioChannelState('system_audio')
    for (let end = 10_000; end <= 100_000; end += 10_000) {
      appendSystemAudioBuffer(systemAudio, new Float32Array(10_000), 1_000, end)
    }
    markSystemAudioSubmitted(systemAudio, 65_000)

    const snapshot = pendingSystemAudioSnapshot(systemAudio, 100_000, 82_000)

    expect(snapshot?.capturedAtMs).toBe(72_000)
    expect(snapshot?.endedAtMs).toBe(100_000)
    expect(snapshot?.chunks.reduce((total, chunk) => total + chunk.length, 0)).toBe(28_000)
  })

  it('builds a standalone system window from only unsent audio with short pre-roll', () => {
    const systemAudio = createAudioChannelState('system_audio')
    for (let end = 10_000; end <= 40_000; end += 10_000) {
      appendSystemAudioBuffer(systemAudio, new Float32Array(10_000), 1_000, end)
    }
    markSystemAudioSubmitted(systemAudio, 20_000)

    const snapshot = pendingStandaloneSystemAudioSnapshot(
      systemAudio,
      38_000,
      30_000
    )

    expect(snapshot?.capturedAtMs).toBe(29_500)
    expect(snapshot?.endedAtMs).toBe(38_000)
    expect(snapshot?.chunks.reduce((total, chunk) => total + chunk.length, 0)).toBe(8_500)
  })

  it('produces bounded non-overlapping windows during continuous system speech', () => {
    const systemAudio = createAudioChannelState('system_audio')
    const windows: Array<[number, number]> = []

    for (let second = 1; second <= 45; second += 1) {
      const shouldFlush = observeSystemAudioChunk(
        systemAudio,
        new Float32Array(1_000),
        1_000,
        0.2,
        second * 1_000
      )
      if (!shouldFlush || systemAudio.segmentStartedAt === null) continue
      const snapshot = pendingStandaloneSystemAudioSnapshot(
        systemAudio,
        second * 1_000,
        systemAudio.segmentStartedAt
      )
      expect(snapshot).not.toBeNull()
      if (snapshot === null) continue
      windows.push([snapshot.capturedAtMs, snapshot.endedAtMs])
      markSystemAudioSubmitted(systemAudio, snapshot.endedAtMs)
      resetAudioSegment(systemAudio)
    }

    expect(windows).toEqual([
      [0, 8_000],
      [8_000, 16_000],
      [16_000, 24_000],
      [24_000, 32_000],
      [32_000, 40_000]
    ])
    expect(windows.every(([start, end]) => end - start <= 8_000)).toBe(true)
  })
})
