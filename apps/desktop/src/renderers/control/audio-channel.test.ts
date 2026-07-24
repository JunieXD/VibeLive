import { describe, expect, it } from 'vitest'
import {
  createAudioChannelState,
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
})
