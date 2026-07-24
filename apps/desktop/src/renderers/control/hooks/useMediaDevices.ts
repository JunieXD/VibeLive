import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react'
import type { DesktopSource, MediaAccessStatus } from '../../../shared/contracts'
import type { SessionStatus } from '../../../shared/session'
import { AUDIO_SEGMENT_SECONDS, encodePcm16Mono } from '../audio'
import {
  bindMediaStreamToVideo,
  calculateMicrophoneLevel,
  describeMediaError,
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
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([])
  const [selectedMicrophoneId, setSelectedMicrophoneId] = useState('')
  const [microphoneLevel, setMicrophoneLevel] = useState(0)
  const [microphoneReady, setMicrophoneReady] = useState(false)
  const [microphonePermission, setMicrophonePermission] =
    useState<MediaAccessStatus>('unknown')
  const [screenPermission, setScreenPermission] = useState<MediaAccessStatus>('unknown')
  const [transitioning, setTransitioning] = useState(false)

  const videoRef = useRef<HTMLVideoElement>(null)
  const cameraVideoRef = useRef<HTMLVideoElement>(null)
  const capturePipelineVideoRef = useRef<HTMLVideoElement>(null)
  const cameraPipelineVideoRef = useRef<HTMLVideoElement>(null)
  const captureStreamRef = useRef<MediaStream | null>(null)
  const cameraStreamRef = useRef<MediaStream | null>(null)
  const microphoneStreamRef = useRef<MediaStream | null>(null)
  const visualSettingsRef = useRef(visualSettings)
  const audioContextRef = useRef<AudioContext | null>(null)
  const audioProcessorRef = useRef<ScriptProcessorNode | null>(null)
  const audioChunksRef = useRef<Float32Array[]>([])
  const audioSampleCountRef = useRef(0)
  const audioSampleRateRef = useRef(0)
  const audioSegmentStartedAtRef = useRef<number | null>(null)
  const audioSendQueueRef = useRef<Promise<void>>(Promise.resolve())
  const audioSegmentSequenceRef = useRef(0)
  const audioIngestErrorReportedRef = useRef(false)
  const meterFrameRef = useRef<number | null>(null)
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

  const flushAudioSegment = useCallback((includePartial = false): Promise<void> => {
    if (!mediaIngestEnabledRef.current) {
      audioChunksRef.current = []
      audioSampleCountRef.current = 0
      audioSegmentStartedAtRef.current = null
      return audioSendQueueRef.current
    }
    const sampleRate = audioSampleRateRef.current
    const sampleCount = audioSampleCountRef.current
    const minimumSamples = includePartial
      ? Math.round(sampleRate * 0.25)
      : Math.round(sampleRate * AUDIO_SEGMENT_SECONDS)
    if (sampleRate <= 0 || sampleCount < minimumSamples) {
      if (includePartial) {
        audioChunksRef.current = []
        audioSampleCountRef.current = 0
        audioSegmentStartedAtRef.current = null
      }
      return audioSendQueueRef.current
    }

    const chunks = audioChunksRef.current
    const capturedAtMs = audioSegmentStartedAtRef.current ?? Date.now()
    audioChunksRef.current = []
    audioSampleCountRef.current = 0
    audioSegmentStartedAtRef.current = null
    const sequence = audioSegmentSequenceRef.current + 1
    audioSegmentSequenceRef.current = sequence
    const body = encodePcm16Mono(chunks, sampleRate)
    const send = audioSendQueueRef.current.then(() =>
      window.advx.submitAudioSegment({
        inputId: `audio-${capturedAtMs}-${sequence}`,
        capturedAtMs,
        body
      })
    )
    const observed = send.then(
      () => {
        audioIngestErrorReportedRef.current = false
      },
      (error: unknown) => {
        if (!audioIngestErrorReportedRef.current) {
          audioIngestErrorReportedRef.current = true
          onSystemActivityRef.current(
            `音频暂未送达后端：${
              error instanceof Error && error.message ? error.message : '实时连接异常。'
            }`
          )
        }
      }
    )
    audioSendQueueRef.current = observed
    return observed
  }, [])

  const stopMicrophone = useCallback(async (): Promise<void> => {
    if (meterFrameRef.current !== null) cancelAnimationFrame(meterFrameRef.current)
    meterFrameRef.current = null
    const processor = audioProcessorRef.current
    audioProcessorRef.current = null
    if (processor) {
      processor.onaudioprocess = null
      processor.disconnect()
    }
    const stream = microphoneStreamRef.current
    microphoneStreamRef.current = null
    stopMediaStream(stream)
    await flushAudioSegment(true)
    const context = audioContextRef.current
    audioContextRef.current = null
    if (context && context.state !== 'closed') await context.close().catch(() => undefined)
    setMicrophoneLevel(0)
    setMicrophoneReady(false)
  }, [flushAudioSegment])

  const refreshMicrophones = useCallback(async (preferred?: string, id?: number) => {
    const devices = await navigator.mediaDevices.enumerateDevices()
    if (id !== undefined) assertCurrent(id)
    const inputs = devices.filter((device) => device.kind === 'audioinput')
    setMicrophones(inputs)
    setSelectedMicrophoneId((current) => {
      if (inputs.some((device) => device.deviceId === current)) return current
      if (preferred && inputs.some((device) => device.deviceId === preferred)) return preferred
      return inputs[0]?.deviceId ?? ''
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

  const startCapture = useCallback(async (id: number, sourceId: string): Promise<MediaStream> => {
    const accepted = await window.advx.selectDesktopSource(sourceId)
    assertCurrent(id)
    if (!accepted) throw new DOMException('The selected display source is no longer available.', 'NotFoundError')
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: { ideal: 12, max: 20 } },
      audio: false
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
        invalidate()
        captureStreamRef.current = null
        setCaptureStream(null)
        if (visualSettingsRef.current.mode === 'pip' && cameraStreamRef.current) {
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
  }, [assertCurrent, fatalMediaRef, invalidate, sessionStatusRef, stopCamera, stopMicrophone])

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
        invalidate()
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

  const startMicrophone = useCallback(async (id: number, deviceId?: string): Promise<MediaStream> => {
    await stopMicrophone()
    assertCurrent(id)
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        echoCancellation: true,
        noiseSuppression: true
      }
    })
    let context: AudioContext | null = null
    try {
      assertCurrent(id)
      const track = stream.getAudioTracks()[0]
      if (!track) throw new DOMException('No microphone audio track was created.', 'NotReadableError')
      context = new AudioContext()
      const analyser = context.createAnalyser()
      analyser.fftSize = 512
      const source = context.createMediaStreamSource(stream)
      source.connect(analyser)
      const processor = context.createScriptProcessor(4096, 1, 1)
      const silentOutput = context.createGain()
      silentOutput.gain.value = 0
      source.connect(processor)
      processor.connect(silentOutput)
      silentOutput.connect(context.destination)
      processor.onaudioprocess = (event): void => {
        if (sessionStatusRef.current !== 'running' || !mediaIngestEnabledRef.current) return
        const samples = event.inputBuffer.getChannelData(0)
        if (samples.length === 0) return
        if (audioSegmentStartedAtRef.current === null) {
          audioSegmentStartedAtRef.current = Date.now()
        }
        const copy = new Float32Array(samples)
        audioChunksRef.current.push(copy)
        audioSampleCountRef.current += copy.length
        audioSampleRateRef.current = context?.sampleRate ?? event.inputBuffer.sampleRate
        if (
          audioSampleCountRef.current >=
          audioSampleRateRef.current * AUDIO_SEGMENT_SECONDS
        ) {
          void flushAudioSegment()
        }
      }
      if (context.state === 'suspended') {
        await context.resume()
        assertCurrent(id)
      }
      await refreshMicrophones(track.getSettings().deviceId, id)
      assertCurrent(id)
      microphoneStreamRef.current = stream
      audioContextRef.current = context
      audioProcessorRef.current = processor
      audioChunksRef.current = []
      audioSampleCountRef.current = 0
      audioSampleRateRef.current = context.sampleRate
      audioSegmentStartedAtRef.current = null
      setMicrophoneReady(true)
      setMicrophonePermission('granted')
      track.addEventListener('ended', () => {
        if (microphoneStreamRef.current !== stream) return
        invalidate()
        microphoneStreamRef.current = null
        const activeProcessor = audioProcessorRef.current
        audioProcessorRef.current = null
        if (activeProcessor) {
          activeProcessor.onaudioprocess = null
          activeProcessor.disconnect()
        }
        if (meterFrameRef.current !== null) cancelAnimationFrame(meterFrameRef.current)
        meterFrameRef.current = null
        void flushAudioSegment(true)
        void context?.close()
        audioContextRef.current = null
        setMicrophoneLevel(0)
        setMicrophoneReady(false)
        if (sessionStatusRef.current === 'running' || sessionStatusRef.current === 'starting') {
          onSystemActivityRef.current('麦克风连接已中断，继续进行仅画面直播。')
        }
      }, { once: true })
      const samples = new Uint8Array(analyser.fftSize)
      const measure = (): void => {
        if (microphoneStreamRef.current !== stream) return
        analyser.getByteTimeDomainData(samples)
        setMicrophoneLevel(calculateMicrophoneLevel(samples))
        meterFrameRef.current = requestAnimationFrame(measure)
      }
      measure()
      return stream
    } catch (error) {
      stopMediaStream(stream)
      if (context && context.state !== 'closed') await context.close().catch(() => undefined)
      throw error
    }
  }, [assertCurrent, fatalMediaRef, flushAudioSegment, invalidate, refreshMicrophones, sessionStatusRef, stopCapture, stopMicrophone])

  const chooseSource = useCallback(async (source: DesktopSource) => {
    const id = begin()
    if (id === null) return
    try {
      await startCapture(id, source.id)
      setSelectedSource(source)
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
  }, [begin, cameraEnabled, finish, isCurrent, startCapture])

  const requestMicrophoneAccess = useCallback(async () => {
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
  }, [begin, finish, isCurrent, selectedMicrophoneId, startMicrophone, stopMicrophone])

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
      if (!requirements.screen) stopCapture()
      if (!requirements.camera) stopCamera()
    } catch (error) {
      if (!isCurrent(id)) return
      if (!previousDisplay && captureStreamRef.current) stopCapture()
      if (!previousCamera && cameraStreamRef.current) stopCamera()
      onSystemActivityRef.current(describeMediaError(error, requirements.camera && !previousCamera ? 'camera' : 'display'))
    } finally {
      finish(id)
    }
  }, [assertCurrent, begin, cameraEnabled, finish, isCurrent, selectedSource, startCamera, startCapture, stopCamera, stopCapture])

  const changeMicrophone = useCallback(async (deviceId: string) => {
    setSelectedMicrophoneId(deviceId)
    if (!microphoneReady) return
    const id = begin()
    if (id === null) return
    try {
      await startMicrophone(id, deviceId)
    } catch (error) {
      if (!isCurrent(id)) return
      await stopMicrophone()
      onSystemActivityRef.current(describeMediaError(error, 'microphone'))
    } finally {
      finish(id)
    }
  }, [begin, finish, isCurrent, microphoneReady, startMicrophone, stopMicrophone])

  useEffect(() => () => {
    operationIdRef.current += 1
    transitionRef.current = false
    stopMediaStream(captureStreamRef.current)
    stopMediaStream(cameraStreamRef.current)
    void window.advx.cancelCameraCaptureAuthorization().catch(() => undefined)
    void stopMicrophone()
  }, [stopMicrophone])

  return {
    selectedSource, setSelectedSource, captureStream, cameraStream, cameras, cameraEnabled,
    cameraPermission, visualSettings, setVisualSettings, microphones, selectedMicrophoneId,
    microphoneLevel, microphoneReady, microphonePermission, screenPermission, videoRef,
    cameraVideoRef, capturePipelineVideoRef, cameraPipelineVideoRef, captureStreamRef,
    cameraStreamRef, microphoneStreamRef, visualSettingsRef,
    operation: { begin, finish, assertCurrent, isCurrent, invalidate, transitioning },
    chooseSource, requestMicrophoneAccess, toggleCamera, changeCamera, changeVisualMode,
    changeMicrophone, startCapture, startCamera, startMicrophone, stopCapture, stopCamera,
    stopMicrophone
  }
}
