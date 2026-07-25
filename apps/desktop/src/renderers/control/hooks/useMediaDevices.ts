import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react'
import type { DesktopSource, MediaAccessStatus } from '../../../shared/contracts'
import type { SessionStatus } from '../../../shared/session'
import {
  clearSystemAudioBuffer,
  createAudioChannelState,
  markSystemAudioSubmitted,
  observeSystemAudioChunk,
  pendingStandaloneSystemAudioSnapshot,
  pendingSystemAudioSnapshot,
  releaseFailedLoopbackCapture,
  resetAudioSegment,
  resetSpeechGate,
  shouldReleaseLoopbackVideo,
  updateAudioTransportError,
  type AudioChannelState
} from '../audio-channel'
import { loadAudioSettings, saveAudioSettings } from '../audio-settings'
import {
  AUDIO_SENTENCE_SILENCE_SECONDS,
  AUDIO_SPEECH_CONFIRMATION_MS,
  encodePcm16Mono,
  shouldHardFlushMicrophoneSegment,
  speechThresholds,
  updateNoiseFloor
} from '../audio'
import {
  bindMediaStreamToVideo,
  calculateMicrophoneLevel,
  describeMediaError,
  getDefaultDesktopSource,
  stopMediaStream
} from '../media'
import {
  loadVisualSettings,
  requiredVisualSources,
  saveVisualSettings,
  type VisualMode,
  type VisualSettings
} from '../visual'
import type { FatalMediaKind, MediaDevicesController } from './mediaControllerTypes'

type UseMediaDevicesOptions = {
  sessionStatusRef: MutableRefObject<SessionStatus>
  fatalMediaRef: MutableRefObject<(kind: FatalMediaKind, error: string) => void>
  mediaIngestEnabledRef: MutableRefObject<boolean>
  onSystemActivity: (text: string) => void
  onRequestSourcePicker: () => void
}

export function useMediaDevices({
  sessionStatusRef,
  fatalMediaRef,
  mediaIngestEnabledRef,
  onSystemActivity,
  onRequestSourcePicker
}: UseMediaDevicesOptions): MediaDevicesController {
  const [selectedSource, setSelectedSource] = useState<DesktopSource | null>(null)
  const [captureStream, setCaptureStream] = useState<MediaStream | null>(null)
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null)
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([])
  const [cameraEnabled, setCameraEnabled] = useState(false)
  const [cameraPermission, setCameraPermission] = useState<MediaAccessStatus>('unknown')
  const [visualSettings, setVisualSettings] = useState<VisualSettings>(() =>
    loadVisualSettings(window.localStorage)
  )
  const [audioSettings, setAudioSettings] = useState(() =>
    loadAudioSettings(window.localStorage)
  )
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([])
  const selectedMicrophoneId = audioSettings.selectedMicrophoneId
  const [microphoneLevel, setMicrophoneLevel] = useState(0)
  const [microphoneReady, setMicrophoneReady] = useState(false)
  const [microphonePermission, setMicrophonePermission] =
    useState<MediaAccessStatus>('unknown')
  const [microphoneTransportError, setMicrophoneTransportError] = useState<string | null>(null)
  const [systemAudioSupported, setSystemAudioSupported] = useState(false)
  const [systemAudioLevel, setSystemAudioLevel] = useState(0)
  const [systemAudioReady, setSystemAudioReady] = useState(false)
  const [systemAudioError, setSystemAudioError] = useState<string | null>(null)
  const [systemAudioTransportError, setSystemAudioTransportError] =
    useState<string | null>(null)
  const [screenPermission, setScreenPermission] = useState<MediaAccessStatus>('unknown')
  const [transitioning, setTransitioning] = useState(false)

  const videoRef = useRef<HTMLVideoElement>(null)
  const cameraVideoRef = useRef<HTMLVideoElement>(null)
  const capturePipelineVideoRef = useRef<HTMLVideoElement>(null)
  const cameraPipelineVideoRef = useRef<HTMLVideoElement>(null)
  const captureStreamRef = useRef<MediaStream | null>(null)
  const cameraStreamRef = useRef<MediaStream | null>(null)
  const microphoneStreamRef = useRef<MediaStream | null>(null)
  const pendingMicrophoneStreamRef = useRef<MediaStream | null>(null)
  const visualSettingsRef = useRef(visualSettings)
  const microphoneChannelRef = useRef(createAudioChannelState('microphone'))
  const systemAudioChannelRef = useRef(createAudioChannelState('system_audio'))
  const operationIdRef = useRef(0)
  const transitionRef = useRef(false)
  const onSystemActivityRef = useRef(onSystemActivity)
  const onRequestSourcePickerRef = useRef(onRequestSourcePicker)
  onSystemActivityRef.current = onSystemActivity
  onRequestSourcePickerRef.current = onRequestSourcePicker

  const begin = useCallback((replaceCurrent = false): number | null => {
    if (transitionRef.current && !replaceCurrent) return null
    const operationId = ++operationIdRef.current
    transitionRef.current = true
    setTransitioning(true)
    return operationId
  }, [])
  const finish = useCallback((operationId: number): void => {
    if (operationIdRef.current !== operationId) return
    transitionRef.current = false
    setTransitioning(false)
  }, [])
  const assertCurrent = useCallback((operationId: number): void => {
    if (operationIdRef.current !== operationId) {
      throw new DOMException('Media operation was superseded.', 'AbortError')
    }
  }, [])
  const isCurrent = useCallback((operationId: number) => operationIdRef.current === operationId, [])
  const invalidate = useCallback(() => {
    operationIdRef.current += 1
    transitionRef.current = false
    setTransitioning(false)
  }, [])

  useEffect(() => {
    visualSettingsRef.current = visualSettings
    saveVisualSettings(window.localStorage, visualSettings)
  }, [visualSettings])
  useEffect(() => {
    saveAudioSettings(window.localStorage, audioSettings)
  }, [audioSettings])
  useEffect(() => {
    captureStreamRef.current = captureStream
    bindMediaStreamToVideo(videoRef.current, captureStream)
    bindMediaStreamToVideo(capturePipelineVideoRef.current, captureStream)
  }, [captureStream])
  useEffect(() => {
    cameraStreamRef.current = cameraStream
    bindMediaStreamToVideo(cameraVideoRef.current, cameraStream)
    bindMediaStreamToVideo(cameraPipelineVideoRef.current, cameraStream)
  }, [cameraStream])
  useEffect(() => {
    void window.advx.getMediaAccessStatus().then((status) => {
      setMicrophonePermission(status.microphone)
      setCameraPermission(status.camera)
      setScreenPermission(status.screen)
      setSystemAudioSupported(status.systemAudioSupported)
    }).catch(() => undefined)
  }, [])

  const stopCapture = useCallback(() => {
    const stream = captureStreamRef.current
    captureStreamRef.current = null
    stopMediaStream(stream)
    if (videoRef.current?.srcObject === stream) {
      bindMediaStreamToVideo(videoRef.current, null)
    }
    if (capturePipelineVideoRef.current?.srcObject === stream) {
      bindMediaStreamToVideo(capturePipelineVideoRef.current, null)
    }
    setCaptureStream(null)
  }, [])
  const stopCamera = useCallback(() => {
    void window.advx.cancelCameraCaptureAuthorization().catch(() => undefined)
    const stream = cameraStreamRef.current
    cameraStreamRef.current = null
    stopMediaStream(stream)
    if (cameraVideoRef.current?.srcObject === stream) {
      bindMediaStreamToVideo(cameraVideoRef.current, null)
    }
    if (cameraPipelineVideoRef.current?.srcObject === stream) {
      bindMediaStreamToVideo(cameraPipelineVideoRef.current, null)
    }
    setCameraStream(null)
  }, [])

  const observeAudioTransport = useCallback((
    channel: AudioChannelState,
    send: Promise<void>
  ): Promise<void> => {
    const observed = send.then(
      () => {
        channel.ingestErrorReported = false
        updateAudioTransportError(
          channel,
          null,
          channel.source === 'microphone'
            ? setMicrophoneTransportError
            : setSystemAudioTransportError
        )
      },
      (error: unknown) => {
        updateAudioTransportError(
          channel,
          error,
          channel.source === 'microphone'
            ? setMicrophoneTransportError
            : setSystemAudioTransportError
        )
        if (!channel.ingestErrorReported) {
          channel.ingestErrorReported = true
          onSystemActivityRef.current(
            `${channel.source === 'microphone' ? '麦克风' : '系统声音'}暂未送达后端：${
              channel.error
            }`
          )
        }
      }
    )
    channel.sendQueue = observed
    return observed
  }, [])

  const enqueueSystemAudioSnapshot = useCallback((
    channel: AudioChannelState,
    snapshot: {
      chunks: Float32Array[]
      sampleRate: number
      capturedAtMs: number
      endedAtMs: number
    },
    turnId?: string
  ): Promise<void> => {
    const sequence = channel.sequence + 1
    channel.sequence = sequence
    channel.systemAudioSubmissionPending = true
    const send = channel.sendQueue
      .then(() => window.advx.submitAudioSegment({
        source: 'system_audio',
        inputId: `system_audio-${snapshot.capturedAtMs}-${snapshot.endedAtMs}-${sequence}`,
        capturedAtMs: snapshot.capturedAtMs,
        body: encodePcm16Mono(snapshot.chunks, snapshot.sampleRate),
        ...(turnId ? { turnId } : {})
      }))
      .then(() => {
        markSystemAudioSubmitted(channel, snapshot.endedAtMs)
      })
      .catch((error: unknown) => {
        if (turnId === undefined) {
          channel.segmentStartedAt = Math.min(
            channel.segmentStartedAt ?? snapshot.capturedAtMs,
            snapshot.capturedAtMs
          )
          channel.lastSpeechAt = Math.max(
            channel.lastSpeechAt ?? snapshot.endedAtMs,
            snapshot.endedAtMs
          )
        }
        throw error
      })
      .finally(() => {
        channel.systemAudioSubmissionPending = false
      })
    observeAudioTransport(channel, send)
    return send
  }, [observeAudioTransport])

  const flushSystemAudioSegment = useCallback((
    channel: AudioChannelState,
    endedAtMs: number,
    preserveMicrophonePreroll = true
  ): Promise<void> => {
    if (
      !mediaIngestEnabledRef.current ||
      channel.systemAudioSubmissionPending ||
      channel.segmentStartedAt === null
    ) {
      if (!mediaIngestEnabledRef.current) resetAudioSegment(channel)
      return channel.sendQueue
    }
    const microphoneChannel = microphoneChannelRef.current
    const microphoneStartedAtMs = preserveMicrophonePreroll
      ? microphoneChannel.segmentStartedAt ?? microphoneChannel.candidateStartedAt
      : null
    const snapshotEndedAtMs = (
      microphoneStartedAtMs === null
        ? endedAtMs
        : Math.min(endedAtMs, microphoneStartedAtMs - 10_000)
    )
    const snapshot = pendingStandaloneSystemAudioSnapshot(
      channel,
      snapshotEndedAtMs,
      channel.segmentStartedAt
    )
    if (snapshot === null) return channel.sendQueue
    resetAudioSegment(channel)
    return enqueueSystemAudioSnapshot(channel, snapshot)
  }, [enqueueSystemAudioSnapshot, mediaIngestEnabledRef])

  const flushMicrophoneSegment = useCallback((channel: AudioChannelState): Promise<void> => {
    if (!mediaIngestEnabledRef.current) {
      resetAudioSegment(channel)
      return channel.sendQueue
    }
    const sampleRate = channel.sampleRate
    const sampleCount = channel.sampleCount
    const minimumSamples = Math.round(sampleRate * 0.1)
    if (sampleRate <= 0 || sampleCount < minimumSamples) {
      resetAudioSegment(channel)
      return channel.sendQueue
    }

    const chunks = channel.chunks
    const capturedAtMs = channel.segmentStartedAt ?? Date.now()
    const endedAtMs = Date.now()
    const systemChannel = systemAudioChannelRef.current
    const systemAudioWasActive = (
      systemChannel.stream !== null ||
      systemChannel.segmentStartedAt !== null
    )
    const sequence = channel.sequence + 1
    channel.sequence = sequence
    resetAudioSegment(channel)
    const microphoneBody = encodePcm16Mono(chunks, sampleRate)
    const send = channel.sendQueue.then(async () => {
      // Select the system range only after the previous turn settles, so a slow
      // upload cannot cause this turn to submit already-consumed audio again.
      await systemChannel.sendQueue
      const systemSnapshot = systemAudioWasActive
        ? pendingSystemAudioSnapshot(systemChannel, endedAtMs, capturedAtMs)
        : null
      const turnId = crypto.randomUUID()
      let systemSubmission: {
        snapshot: NonNullable<typeof systemSnapshot>
        send: Promise<void>
      } | null = null
      if (systemSnapshot !== null) {
        resetAudioSegment(systemChannel)
        systemSubmission = {
          snapshot: systemSnapshot,
          send: enqueueSystemAudioSnapshot(systemChannel, systemSnapshot, turnId)
        }
      }
      const microphoneSend = window.advx.submitAudioSegment({
        source: 'microphone',
        inputId: `microphone-${capturedAtMs}-${sequence}`,
        capturedAtMs,
        body: microphoneBody,
        turnId,
        systemAudioRequired: systemSnapshot !== null
      })
      if (systemSubmission === null) {
        await microphoneSend
        return
      }
      const submission = systemSubmission
      const [systemResult, microphoneResult] = await Promise.allSettled([
        submission.send,
        microphoneSend
      ])
      if (systemResult.status === 'fulfilled' && systemChannel.stream === null) {
        clearSystemAudioBuffer(systemChannel)
      }
      if (microphoneResult.status === 'rejected') throw microphoneResult.reason
      if (systemResult.status === 'rejected') throw systemResult.reason
    })
    return observeAudioTransport(channel, send)
  }, [enqueueSystemAudioSnapshot, observeAudioTransport])

  const stopMicrophone = useCallback(async (
    preservePendingStream?: MediaStream
  ): Promise<void> => {
    const channel = microphoneChannelRef.current
    const pendingStream = pendingMicrophoneStreamRef.current
    if (pendingStream !== preservePendingStream) {
      pendingMicrophoneStreamRef.current = null
      stopMediaStream(pendingStream)
    }
    if (channel.meterFrame !== null) cancelAnimationFrame(channel.meterFrame)
    channel.meterFrame = null
    const processor = channel.processor
    channel.processor = null
    if (processor) {
      processor.onaudioprocess = null
      processor.disconnect()
    }
    const stream = microphoneStreamRef.current
    microphoneStreamRef.current = null
    channel.stream = null
    stopMediaStream(stream)
    await flushMicrophoneSegment(channel)
    updateAudioTransportError(channel, null, setMicrophoneTransportError)
    const context = channel.context
    channel.context = null
    if (context && context.state !== 'closed') await context.close().catch(() => undefined)
    setMicrophoneLevel(0)
    setMicrophoneReady(false)
  }, [flushMicrophoneSegment])

  const stopSystemAudio = useCallback(async (): Promise<void> => {
    const channel = systemAudioChannelRef.current
    const stream = channel.stream
    channel.stream = null
    if (channel.meterFrame !== null) cancelAnimationFrame(channel.meterFrame)
    channel.meterFrame = null
    const processor = channel.processor
    channel.processor = null
    if (processor) {
      processor.onaudioprocess = null
      processor.disconnect()
    }
    await channel.sendQueue.catch(() => undefined)
    const microphoneChannel = microphoneChannelRef.current
    const preserveForMicrophone = (
      sessionStatusRef.current === 'running' &&
      mediaIngestEnabledRef.current &&
      channel.segmentStartedAt !== null &&
      microphoneChannel.segmentStartedAt !== null
    )
    if (
      sessionStatusRef.current === 'running' &&
      mediaIngestEnabledRef.current &&
      channel.segmentStartedAt !== null &&
      !preserveForMicrophone
    ) {
      await flushSystemAudioSegment(channel, Date.now(), false).catch(() => undefined)
    }
    if (!preserveForMicrophone) {
      clearSystemAudioBuffer(channel)
      resetAudioSegment(channel)
    }
    updateAudioTransportError(channel, null, setSystemAudioTransportError)
    const context = channel.context
    channel.context = null
    if (context && context.state !== 'closed') await context.close().catch(() => undefined)
    channel.level = 0
    stream?.getAudioTracks().forEach((track) => track.stop())
    channel.level = 0
    setSystemAudioLevel(0)
    setSystemAudioReady(false)
    if (shouldReleaseLoopbackVideo(
      visualSettingsRef.current.mode,
      captureStreamRef.current === stream
    )) {
      stopCapture()
    }
  }, [flushSystemAudioSegment, mediaIngestEnabledRef, sessionStatusRef, stopCapture])

  const refreshMicrophones = useCallback(async (preferred?: string, id?: number) => {
    const devices = await navigator.mediaDevices.enumerateDevices()
    if (id !== undefined) assertCurrent(id)
    const inputs = devices.filter((device) => device.kind === 'audioinput')
    setMicrophones(inputs)
    setAudioSettings((current) => {
      if (inputs.some((device) => device.deviceId === current.selectedMicrophoneId)) {
        return current
      }
      const selectedMicrophoneId =
        preferred && inputs.some((device) => device.deviceId === preferred)
          ? preferred
          : inputs[0]?.deviceId ?? ''
      return selectedMicrophoneId === current.selectedMicrophoneId
        ? current
        : { ...current, selectedMicrophoneId }
    })
  }, [assertCurrent])
  const refreshCameras = useCallback(async (preferred?: string, id?: number) => {
    const devices = await navigator.mediaDevices.enumerateDevices()
    if (id !== undefined) assertCurrent(id)
    const inputs = devices.filter((device) => device.kind === 'videoinput')
    setCameras(inputs)
    setVisualSettings((current) => {
      if (inputs.some((device) => device.deviceId === current.cameraDeviceId)) return current
      const cameraDeviceId =
        preferred && inputs.some((device) => device.deviceId === preferred)
          ? preferred
          : inputs[0]?.deviceId ?? ''
      return cameraDeviceId === current.cameraDeviceId
        ? current
        : { ...current, cameraDeviceId }
    })
  }, [assertCurrent])
  useEffect(() => {
    const handleChange = (): void => {
      void refreshMicrophones()
      void refreshCameras()
    }
    void refreshMicrophones()
    void refreshCameras()
    navigator.mediaDevices.addEventListener('devicechange', handleChange)
    return () => navigator.mediaDevices.removeEventListener('devicechange', handleChange)
  }, [refreshCameras, refreshMicrophones])

  const startCapture = useCallback(async (
    id: number,
    sourceId: string,
    includeSystemAudio = false
  ): Promise<MediaStream> => {
    const accepted = await window.advx.selectDesktopSource(sourceId)
    assertCurrent(id)
    if (!accepted) throw new DOMException('The selected display source is no longer available.', 'NotFoundError')
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: { ideal: 12, max: 20 } },
      audio: includeSystemAudio
    })
    try {
      assertCurrent(id)
      const track = stream.getVideoTracks()[0]
      if (!track) throw new DOMException('No display video track was created.', 'NotReadableError')
      const previous = captureStreamRef.current
      captureStreamRef.current = stream
      stopMediaStream(previous)
      setCaptureStream(stream)
      setScreenPermission('granted')
      track.addEventListener('ended', () => {
        if (captureStreamRef.current !== stream) return
        if (sessionStatusRef.current !== 'stopping') invalidate()
        captureStreamRef.current = null
        setCaptureStream(null)
        void stopSystemAudio()
        if (visualSettingsRef.current.mode === 'camera' && cameraStreamRef.current) {
          setSystemAudioError('系统声音已断开')
          onSystemActivityRef.current('系统声音来源已结束，摄像头和麦克风继续运行。')
        } else if (visualSettingsRef.current.mode === 'pip' && cameraStreamRef.current) {
          setVisualSettings((current) => ({ ...current, mode: 'camera' }))
          onSystemActivityRef.current('屏幕来源已断开，已自动切换为摄像头画面。')
        } else if (sessionStatusRef.current === 'running' || sessionStatusRef.current === 'starting') {
          stopCamera()
          void stopMicrophone()
          fatalMediaRef.current('display', '画面来源已结束，请重新选择。')
        }
      }, { once: true })
      return stream
    } catch (error) {
      stopMediaStream(stream)
      throw error
    }
  }, [assertCurrent, fatalMediaRef, invalidate, sessionStatusRef, stopCamera, stopMicrophone, stopSystemAudio])

  const startCamera = useCallback(async (id: number, deviceId?: string): Promise<MediaStream> => {
    const authorized = await window.advx.authorizeCameraCapture()
    assertCurrent(id)
    if (!authorized) throw new DOMException('Camera access is denied by the operating system.', 'NotAllowedError')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          width: { ideal: 1280 },
          height: { ideal: 720 },
          frameRate: { ideal: 15, max: 24 }
        },
        audio: false
      })
    } finally {
      await window.advx.cancelCameraCaptureAuthorization().catch(() => undefined)
    }
    try {
      assertCurrent(id)
      const track = stream.getVideoTracks()[0]
      if (!track) throw new DOMException('No camera video track was created.', 'NotReadableError')
      await refreshCameras(track.getSettings().deviceId, id)
      assertCurrent(id)
      const previous = cameraStreamRef.current
      cameraStreamRef.current = stream
      stopMediaStream(previous)
      setCameraStream(stream)
      setCameraEnabled(true)
      setCameraPermission('granted')
      track.addEventListener('ended', () => {
        if (cameraStreamRef.current !== stream) return
        if (sessionStatusRef.current !== 'stopping') invalidate()
        cameraStreamRef.current = null
        setCameraStream(null)
        setCameraEnabled(false)
        void refreshCameras()
        if (visualSettingsRef.current.mode === 'pip' && captureStreamRef.current) {
          setVisualSettings((current) => ({ ...current, mode: 'screen' }))
          onSystemActivityRef.current('摄像头已断开，已自动切换为屏幕画面。')
        } else if (sessionStatusRef.current === 'running' || sessionStatusRef.current === 'starting') {
          stopCapture()
          void stopMicrophone()
          fatalMediaRef.current('camera', '摄像头连接已中断，请检查设备。')
        }
      }, { once: true })
      return stream
    } catch (error) {
      stopMediaStream(stream)
      throw error
    }
  }, [assertCurrent, fatalMediaRef, invalidate, refreshCameras, sessionStatusRef, stopCapture, stopMicrophone])

  const attachAudioProcessing = useCallback(async (
    id: number,
    stream: MediaStream,
    channel: AudioChannelState,
    setLevel: (level: number) => void
  ): Promise<void> => {
    const track = stream.getAudioTracks()[0]
    if (!track) throw new DOMException('No audio track was created.', 'NotReadableError')
    const context = new AudioContext()
    try {
      const analyser = context.createAnalyser()
      analyser.fftSize = 512
      const source = context.createMediaStreamSource(stream)
      const processor = context.createScriptProcessor(4096, 1, 1)
      const silentOutput = context.createGain()
      silentOutput.gain.value = 0
      source.connect(analyser)
      source.connect(processor)
      processor.connect(silentOutput)
      silentOutput.connect(context.destination)
      processor.onaudioprocess = (event): void => {
        if (sessionStatusRef.current !== 'running' || !mediaIngestEnabledRef.current) return
        const samples = event.inputBuffer.getChannelData(0)
        if (samples.length === 0) return
        const level = Math.sqrt(
          samples.reduce((total, sample) => total + sample * sample, 0) / samples.length
        )
        const now = Date.now()
        const sampleRate = context.sampleRate || event.inputBuffer.sampleRate
        const copy = new Float32Array(samples)
        channel.sampleRate = sampleRate
        if (channel.source === 'system_audio') {
          if (observeSystemAudioChunk(channel, copy, sampleRate, level, now)) {
            void flushSystemAudioSegment(channel, now)
          }
          return
        }

        const thresholds = speechThresholds(channel.noiseFloor)
        if (channel.segmentStartedAt === null) {
          channel.noiseFloor = updateNoiseFloor(channel.noiseFloor, level)
          if (level < thresholds.start) {
            channel.candidateChunks = []
            channel.candidateStartedAt = null
            return
          }
          if (channel.candidateStartedAt === null) channel.candidateStartedAt = now
          channel.candidateChunks.push(copy)
          if (now - channel.candidateStartedAt < AUDIO_SPEECH_CONFIRMATION_MS) return
          channel.segmentStartedAt = channel.candidateStartedAt
          channel.chunks = channel.candidateChunks
          channel.sampleCount = channel.chunks.reduce((total, chunk) => total + chunk.length, 0)
          channel.candidateChunks = []
          channel.candidateStartedAt = null
          channel.lastSpeechAt = now
          return
        }
        channel.chunks.push(copy)
        channel.sampleCount += copy.length
        if (shouldHardFlushMicrophoneSegment(channel.segmentStartedAt, now)) {
          void flushMicrophoneSegment(channel)
          channel.segmentStartedAt = now
          channel.lastSpeechAt = now
          return
        }
        if (level >= thresholds.continue) {
          channel.lastSpeechAt = now
        } else if (
          channel.lastSpeechAt !== null &&
          now - channel.lastSpeechAt >= AUDIO_SENTENCE_SILENCE_SECONDS * 1_000
        ) {
          void flushMicrophoneSegment(channel)
        }
      }
      if (context.state === 'suspended') {
        await context.resume()
        assertCurrent(id)
      }
      channel.stream = stream
      channel.context = context
      channel.processor = processor
      channel.sampleRate = context.sampleRate
      if (channel.source === 'microphone') resetSpeechGate(channel)
      else {
        resetAudioSegment(channel)
        clearSystemAudioBuffer(channel)
      }
      const meterSamples = new Uint8Array(analyser.fftSize)
      const measure = (): void => {
        if (channel.stream !== stream) return
        analyser.getByteTimeDomainData(meterSamples)
        channel.level = calculateMicrophoneLevel(meterSamples)
        setLevel(channel.level)
        channel.meterFrame = requestAnimationFrame(measure)
      }
      measure()
    } catch (error) {
      if (context.state !== 'closed') await context.close().catch(() => undefined)
      throw error
    }
  }, [assertCurrent, flushMicrophoneSegment, flushSystemAudioSegment, sessionStatusRef])

  const startMicrophone = useCallback(async (id: number, deviceId?: string): Promise<MediaStream> => {
    assertCurrent(id)
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        echoCancellation: true,
        noiseSuppression: true
      }
    })
    pendingMicrophoneStreamRef.current = stream
    let previousStreamReleased = false
    try {
      assertCurrent(id)
      const track = stream.getAudioTracks()[0]
      if (!track) throw new DOMException('No microphone audio track was created.', 'NotReadableError')
      await stopMicrophone(stream)
      previousStreamReleased = true
      assertCurrent(id)
      await attachAudioProcessing(id, stream, microphoneChannelRef.current, setMicrophoneLevel)
      await refreshMicrophones(track.getSettings().deviceId, id)
      assertCurrent(id)
      microphoneStreamRef.current = stream
      if (pendingMicrophoneStreamRef.current === stream) {
        pendingMicrophoneStreamRef.current = null
      }
      setMicrophoneReady(true)
      setMicrophonePermission('granted')
      track.addEventListener('ended', () => {
        if (microphoneStreamRef.current !== stream) return
        void stopMicrophone()
        if (sessionStatusRef.current === 'running' || sessionStatusRef.current === 'starting') {
          onSystemActivityRef.current('麦克风连接已中断，继续进行仅画面直播。')
        }
      }, { once: true })
      return stream
    } catch (error) {
      if (pendingMicrophoneStreamRef.current === stream) {
        pendingMicrophoneStreamRef.current = null
      }
      stopMediaStream(stream)
      if (previousStreamReleased || microphoneStreamRef.current === stream) {
        await stopMicrophone()
      }
      throw error
    }
  }, [assertCurrent, attachAudioProcessing, refreshMicrophones, sessionStatusRef, stopMicrophone])

  const activateSystemAudioStream = useCallback(async (
    id: number,
    stream: MediaStream
  ): Promise<void> => {
    try {
      assertCurrent(id)
      const track = stream.getAudioTracks()[0]
      if (!track) throw new DOMException('The selected source did not provide loopback audio.', 'NotReadableError')
      await attachAudioProcessing(id, stream, systemAudioChannelRef.current, setSystemAudioLevel)
      setSystemAudioReady(true)
      setSystemAudioError(null)
      track.addEventListener('ended', () => {
        if (systemAudioChannelRef.current.stream !== stream) return
        void stopSystemAudio()
        setSystemAudioError('系统声音已断开')
        onSystemActivityRef.current('系统声音已断开，麦克风和直播继续运行。')
      }, { once: true })
    } catch (error) {
      await stopSystemAudio()
      setSystemAudioError(describeMediaError(error, 'display'))
      throw error
    }
  }, [assertCurrent, attachAudioProcessing, stopSystemAudio])

  const startSystemAudio = useCallback(async (id: number): Promise<void> => {
    await stopSystemAudio()
    assertCurrent(id)
    if (!systemAudioSupported) return
    if (!selectedSource) {
      throw new DOMException('No display source is available for system audio.', 'NotFoundError')
    }
    const stream = await startCapture(id, selectedSource.id, true)
    try {
      await activateSystemAudioStream(id, stream)
    } catch (error) {
      releaseFailedLoopbackCapture(
        visualSettingsRef.current.mode,
        stream,
        captureStreamRef.current,
        stopCapture
      )
      throw error
    }
  }, [
    activateSystemAudioStream,
    assertCurrent,
    selectedSource,
    startCapture,
    stopSystemAudio,
    stopCapture,
    systemAudioSupported
  ])

  const chooseSource = useCallback(async (source: DesktopSource) => {
    const id = begin()
    if (id === null) return
    try {
      const includeSystemAudio =
        sessionStatusRef.current === 'running' &&
        audioSettings.systemAudioEnabled &&
        systemAudioSupported
      if (includeSystemAudio) await stopSystemAudio()
      const stream = await startCapture(id, source.id, includeSystemAudio)
      setSelectedSource(source)
      if (includeSystemAudio) {
        try {
          await activateSystemAudioStream(id, stream)
        } catch (error) {
          releaseFailedLoopbackCapture(
            visualSettingsRef.current.mode,
            stream,
            captureStreamRef.current,
            stopCapture
          )
          onSystemActivityRef.current(
            `新画面没有可用的系统声音：${describeMediaError(error, 'display')} 麦克风和直播继续运行。`
          )
        }
      }
      if (cameraEnabled && cameraStreamRef.current) {
        setVisualSettings((current) => ({ ...current, mode: 'pip' }))
      }
    } catch (error) {
      if (!isCurrent(id)) return
      void window.advx.getMediaAccessStatus().then((status) => setScreenPermission(status.screen)).catch(() => undefined)
      onSystemActivityRef.current(describeMediaError(error, 'display'))
    } finally {
      finish(id)
    }
  }, [
    activateSystemAudioStream,
    audioSettings.systemAudioEnabled,
    begin,
    cameraEnabled,
    finish,
    isCurrent,
    sessionStatusRef,
    startCapture,
    stopCapture,
    stopSystemAudio,
    systemAudioSupported
  ])

  useEffect(() => {
    if (selectedSource) return
    let cancelled = false

    void window.advx
      .listDesktopSources()
      .then(async (sources) => {
        if (cancelled || captureStreamRef.current) return
        const source = getDefaultDesktopSource(sources)
        if (!source) return
        const id = begin()
        if (id === null) return
        try {
          await startCapture(id, source.id)
          if (!cancelled && isCurrent(id)) setSelectedSource(source)
        } catch (error) {
          if (!cancelled && isCurrent(id)) {
            void window.advx
              .getMediaAccessStatus()
              .then((status) => setScreenPermission(status.screen))
              .catch(() => undefined)
            onSystemActivityRef.current(describeMediaError(error, 'display'))
          }
        } finally {
          finish(id)
        }
      })
      .catch(() => {
        if (!cancelled) {
          onSystemActivityRef.current('无法读取默认桌面来源，请检查系统录屏权限。')
        }
      })

    return () => {
      cancelled = true
    }
  }, [begin, finish, isCurrent, selectedSource, startCapture])

  const requestMicrophoneAccess = useCallback(async () => {
    if (!audioSettings.microphoneEnabled) return
    const id = begin()
    if (id === null) return
    try {
      const status = await window.advx.requestMicrophonePermission()
      if (!isCurrent(id)) return
      setMicrophonePermission(status)
      if (status === 'denied' || status === 'restricted') {
        throw new DOMException('Microphone access is denied by the operating system.', 'NotAllowedError')
      }
      await startMicrophone(id, selectedMicrophoneId || undefined)
    } catch (error) {
      if (!isCurrent(id)) return
      await stopMicrophone()
      onSystemActivityRef.current(describeMediaError(error, 'microphone'))
    } finally {
      finish(id)
    }
  }, [
    audioSettings.microphoneEnabled,
    begin,
    finish,
    isCurrent,
    selectedMicrophoneId,
    startMicrophone,
    stopMicrophone
  ])

  const toggleCamera = useCallback(async () => {
    const id = begin(true)
    if (id === null) return
    if (cameraEnabled && cameraStreamRef.current) {
      stopCamera()
      setCameraEnabled(false)
      if (captureStreamRef.current) {
        setVisualSettings((current) => ({ ...current, mode: 'screen' }))
        onSystemActivityRef.current('摄像头已关闭，继续使用屏幕画面。')
      } else if (sessionStatusRef.current === 'running' || sessionStatusRef.current === 'starting') {
        void stopMicrophone()
        fatalMediaRef.current('camera', '唯一的摄像头画面已关闭，视觉采样已停止。')
      } else {
        setVisualSettings((current) => ({ ...current, mode: 'screen' }))
      }
      finish(id)
      return
    }
    let startedCamera: MediaStream | null = null
    let startedDisplay: MediaStream | null = null
    try {
      const status = await window.advx.requestCameraPermission()
      assertCurrent(id)
      setCameraPermission(status)
      if (status === 'denied' || status === 'restricted') {
        throw new DOMException('Camera access is denied by the operating system.', 'NotAllowedError')
      }
      startedCamera = await startCamera(id, visualSettingsRef.current.cameraDeviceId || undefined)
      let hasScreen = captureStreamRef.current !== null
      if (!hasScreen && selectedSource) {
        try {
          startedDisplay = await startCapture(id, selectedSource.id)
          hasScreen = true
        } catch (error) {
          if (!isCurrent(id)) return
          onSystemActivityRef.current(`${describeMediaError(error, 'display')} 已改用摄像头全屏。`)
        }
      }
      assertCurrent(id)
      setCameraEnabled(true)
      setVisualSettings((current) => ({ ...current, mode: hasScreen ? 'pip' : 'camera' }))
    } catch (error) {
      if (!isCurrent(id)) return
      if (cameraStreamRef.current === startedCamera) stopCamera()
      if (captureStreamRef.current === startedDisplay) stopCapture()
      setCameraEnabled(false)
      onSystemActivityRef.current(describeMediaError(error, 'camera'))
    } finally {
      finish(id)
    }
  }, [assertCurrent, begin, cameraEnabled, fatalMediaRef, finish, isCurrent, selectedSource, sessionStatusRef, startCamera, startCapture, stopCamera, stopCapture, stopMicrophone])

  const changeCamera = useCallback(async (deviceId: string) => {
    const previous = visualSettingsRef.current.cameraDeviceId
    setVisualSettings((current) => ({ ...current, cameraDeviceId: deviceId }))
    if (!cameraStreamRef.current) return
    const id = begin()
    if (id === null) return
    try {
      await startCamera(id, deviceId || undefined)
    } catch (error) {
      if (!isCurrent(id)) return
      setVisualSettings((current) => ({ ...current, cameraDeviceId: previous }))
      onSystemActivityRef.current(describeMediaError(error, 'camera'))
    } finally {
      finish(id)
    }
  }, [begin, finish, isCurrent, startCamera])

  const changeVisualMode = useCallback(async (mode: VisualMode) => {
    if (mode === visualSettingsRef.current.mode) return
    const requirements = requiredVisualSources(mode)
    if (requirements.screen && !selectedSource) {
      onRequestSourcePickerRef.current()
      return
    }
    if (requirements.camera && !cameraEnabled) {
      onSystemActivityRef.current('请先显式开启摄像头。')
      return
    }
    const id = begin()
    if (id === null) return
    const previousDisplay = captureStreamRef.current
    const previousCamera = cameraStreamRef.current
    try {
      if (requirements.screen && !captureStreamRef.current) await startCapture(id, selectedSource?.id ?? '')
      if (requirements.camera && !cameraStreamRef.current) {
        await startCamera(id, visualSettingsRef.current.cameraDeviceId || undefined)
      }
      assertCurrent(id)
      setVisualSettings((current) => ({ ...current, mode }))
      if (!requirements.screen && !systemAudioReady) stopCapture()
      if (!requirements.camera) stopCamera()
    } catch (error) {
      if (!isCurrent(id)) return
      if (!previousDisplay && captureStreamRef.current) stopCapture()
      if (!previousCamera && cameraStreamRef.current) stopCamera()
      onSystemActivityRef.current(describeMediaError(error, requirements.camera && !previousCamera ? 'camera' : 'display'))
    } finally {
      finish(id)
    }
  }, [assertCurrent, begin, cameraEnabled, finish, isCurrent, selectedSource, startCamera, startCapture, stopCamera, stopCapture, systemAudioReady])

  const changeMicrophone = useCallback(async (deviceId: string) => {
    const previousDeviceId = selectedMicrophoneId
    setAudioSettings((current) => ({ ...current, selectedMicrophoneId: deviceId }))
    if (
      !audioSettings.microphoneEnabled ||
      (!microphoneReady && sessionStatusRef.current !== 'running')
    ) {
      return
    }
    const id = begin()
    if (id === null) return
    try {
      await startMicrophone(id, deviceId)
    } catch (error) {
      if (!isCurrent(id)) return
      setAudioSettings((current) => ({
        ...current,
        selectedMicrophoneId: previousDeviceId
      }))
      if (!microphoneStreamRef.current && previousDeviceId) {
        try {
          await startMicrophone(id, previousDeviceId)
        } catch {
          await stopMicrophone()
        }
      }
      onSystemActivityRef.current(describeMediaError(error, 'microphone'))
    } finally {
      finish(id)
    }
  }, [
    audioSettings.microphoneEnabled,
    begin,
    finish,
    isCurrent,
    microphoneReady,
    selectedMicrophoneId,
    sessionStatusRef,
    startMicrophone,
    stopMicrophone
  ])

  const toggleMicrophone = useCallback(async () => {
    const enabled = !audioSettings.microphoneEnabled
    setAudioSettings((current) => ({ ...current, microphoneEnabled: enabled }))
    setMicrophoneTransportError(null)

    if (!enabled) {
      const id = begin(true)
      if (id === null) return
      try {
        await stopMicrophone()
        if (sessionStatusRef.current === 'running') {
          onSystemActivityRef.current('麦克风已关闭，系统声音和直播继续运行。')
        }
      } finally {
        finish(id)
      }
      return
    }
    if (sessionStatusRef.current !== 'running') return

    const id = begin(true)
    if (id === null) return
    try {
      const status = await window.advx.requestMicrophonePermission()
      if (!isCurrent(id)) return
      setMicrophonePermission(status)
      if (status === 'denied' || status === 'restricted') {
        throw new DOMException(
          'Microphone access is denied by the operating system.',
          'NotAllowedError'
        )
      }
      await startMicrophone(id, selectedMicrophoneId || undefined)
    } catch (error) {
      if (!isCurrent(id)) return
      await stopMicrophone()
      onSystemActivityRef.current(
        `麦克风未能启用：${describeMediaError(error, 'microphone')} 系统声音和直播继续运行。`
      )
    } finally {
      finish(id)
    }
  }, [
    audioSettings.microphoneEnabled,
    begin,
    finish,
    isCurrent,
    selectedMicrophoneId,
    sessionStatusRef,
    startMicrophone,
    stopMicrophone
  ])

  const toggleSystemAudio = useCallback(async () => {
    if (!systemAudioSupported) return
    const enabled = !audioSettings.systemAudioEnabled
    setAudioSettings((current) => ({ ...current, systemAudioEnabled: enabled }))
    setSystemAudioError(null)
    if (sessionStatusRef.current !== 'running') {
      if (!enabled) await stopSystemAudio()
      return
    }
    const id = begin(true)
    if (id === null) return
    try {
      if (enabled) await startSystemAudio(id)
      else await stopSystemAudio()
    } catch (error) {
      if (!isCurrent(id)) return
      const detail = describeMediaError(error, 'display')
      setSystemAudioError(detail)
      onSystemActivityRef.current(`系统声音未能启用：${detail} 麦克风和直播继续运行。`)
    } finally {
      finish(id)
    }
  }, [
    audioSettings.systemAudioEnabled,
    begin,
    finish,
    isCurrent,
    sessionStatusRef,
    startSystemAudio,
    stopSystemAudio,
    systemAudioSupported
  ])

  useEffect(() => () => {
    operationIdRef.current += 1
    transitionRef.current = false
    stopMediaStream(captureStreamRef.current)
    stopMediaStream(cameraStreamRef.current)
    void window.advx.cancelCameraCaptureAuthorization().catch(() => undefined)
    void stopMicrophone()
    void stopSystemAudio()
  }, [stopMicrophone, stopSystemAudio])

  return {
    selectedSource, setSelectedSource, captureStream, cameraStream, cameras, cameraEnabled,
    cameraPermission, visualSettings, setVisualSettings, microphones, selectedMicrophoneId,
    microphoneEnabled: audioSettings.microphoneEnabled,
    microphoneLevel, microphoneReady, microphonePermission, microphoneTransportError,
    systemAudioEnabled: audioSettings.systemAudioEnabled, systemAudioSupported,
    systemAudioLevel, systemAudioReady, systemAudioError, systemAudioTransportError,
    screenPermission, videoRef,
    cameraVideoRef, capturePipelineVideoRef, cameraPipelineVideoRef, captureStreamRef,
    cameraStreamRef, microphoneStreamRef, visualSettingsRef,
    operation: { begin, finish, assertCurrent, isCurrent, invalidate, transitioning },
    chooseSource, requestMicrophoneAccess, toggleMicrophone, toggleCamera, changeCamera, changeVisualMode,
    changeMicrophone, startCapture, startCamera, startMicrophone, startSystemAudio,
    stopCapture, stopCamera, stopMicrophone, stopSystemAudio, toggleSystemAudio
  }
}
