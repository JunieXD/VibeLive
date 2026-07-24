import { useEffect, useRef, useState, type MutableRefObject, type RefObject } from 'react'
import type { SessionStatus } from '../../../shared/session'
import type { AudienceVisualSettings } from '../../../shared/audience'
import {
  COMPRESSION_PROFILES,
  compressCompositeCanvas,
  deliverAndReleaseVisualBatch,
  drawCompositeFrame,
  grayscaleMeanAbsoluteDifference,
  releaseVisualFrames,
  requiredVisualSources,
  sampleCanvasChangeSignature,
  type VisualBatchSink,
  type VisualFrame,
  type VisualPipelineStatus,
  type VisualSettings
} from '../visual'

type UseVisualPipelineOptions = {
  sessionStatus: SessionStatus
  visualSettings: VisualSettings
  framePolicy: AudienceVisualSettings
  captureStream: MediaStream | null
  cameraStream: MediaStream | null
  captureStreamRef: MutableRefObject<MediaStream | null>
  cameraStreamRef: MutableRefObject<MediaStream | null>
  videoRef: RefObject<HTMLVideoElement | null>
  cameraVideoRef: RefObject<HTMLVideoElement | null>
  deliveryEnabled: boolean
  batchSink?: VisualBatchSink
}

type VisualPipeline = {
  compositeCanvasRef: RefObject<HTMLCanvasElement | null>
  status: VisualPipelineStatus
  lastFrameBytes: number | null
  lastFrameOverTarget: boolean
  lastBatchAt: number | null
}

export function useVisualPipeline({
  sessionStatus,
  visualSettings,
  framePolicy,
  captureStream,
  cameraStream,
  captureStreamRef,
  cameraStreamRef,
  videoRef,
  cameraVideoRef,
  deliveryEnabled,
  batchSink
}: UseVisualPipelineOptions): VisualPipeline {
  const compositeCanvasRef = useRef<HTMLCanvasElement>(null)
  const [status, setStatus] = useState<VisualPipelineStatus>('waiting-backend')
  const [lastFrameBytes, setLastFrameBytes] = useState<number | null>(null)
  const [lastFrameOverTarget, setLastFrameOverTarget] = useState(false)
  const [lastBatchAt, setLastBatchAt] = useState<number | null>(null)

  const sessionStatusRef = useRef(sessionStatus)
  const pendingFramesRef = useRef<VisualFrame[]>([])
  const runRef = useRef(0)
  const sampleBusyRef = useRef<number | null>(null)
  const batchBusyRef = useRef<number | null>(null)
  const frameSequenceRef = useRef(0)
  const defaultBatchSinkRef = useRef<VisualBatchSink>({
    consume: async (batch, signal) => {
      for (const frame of batch.frames) {
        if (signal.aborted) {
          throw new DOMException('Visual delivery aborted.', 'AbortError')
        }
        if (!frame.blob) continue
        const body = new Uint8Array(await frame.blob.arrayBuffer())
        if (signal.aborted) {
          throw new DOMException('Visual delivery aborted.', 'AbortError')
        }
        await window.advx.submitVisualFrame({
          inputId: frame.frameId,
          capturedAtMs: frame.capturedAt,
          mimeType: frame.blob.type || 'image/jpeg',
          changeScore: frame.changeScore,
          body
        })
      }
      return 'accepted'
    }
  })
  const batchSinkRef = useRef<VisualBatchSink>(batchSink ?? defaultBatchSinkRef.current)
  batchSinkRef.current = batchSink ?? defaultBatchSinkRef.current

  useEffect(() => {
    sessionStatusRef.current = sessionStatus
  }, [sessionStatus])

  useEffect(() => {
    const runId = runRef.current + 1
    runRef.current = runId
    releaseVisualFrames(pendingFramesRef.current)
    pendingFramesRef.current = []

    if (sessionStatus !== 'running') {
      setStatus('waiting-backend')
      return
    }

    if (!deliveryEnabled) {
      setStatus('local-preview')
      return
    }

    setStatus('waiting-backend')
    const presetProfile = COMPRESSION_PROFILES[visualSettings.compressionPreset]
    const profile = {
      ...presetProfile,
      maxLongEdge: Math.min(presetProfile.maxLongEdge, framePolicy.frameMaxDimension)
    }
    const signatureCanvas = document.createElement('canvas')
    let previousSignature: Uint8Array | null = null
    const batchAbortController = new AbortController()

    const sampleFrame = async (): Promise<void> => {
      if (sampleBusyRef.current !== null) return
      const requirements = requiredVisualSources(visualSettings.mode)
      const screenVideo = videoRef.current
      const cameraVideo = cameraVideoRef.current
      if (
        (requirements.screen &&
          (!captureStreamRef.current || !screenVideo || screenVideo.videoWidth === 0)) ||
        (requirements.camera &&
          (!cameraStreamRef.current || !cameraVideo || cameraVideo.videoWidth === 0))
      ) {
        return
      }

      const canvas = compositeCanvasRef.current
      if (!canvas) return
      sampleBusyRef.current = runId
      try {
        const primaryVideo = visualSettings.mode === 'camera' ? cameraVideo : screenVideo
        const outputLongEdge = Math.min(
          profile.maxLongEdge,
          Math.max(primaryVideo?.videoWidth ?? 0, primaryVideo?.videoHeight ?? 0)
        )
        if (outputLongEdge <= 0) return
        const drawn = drawCompositeFrame(canvas, {
          mode: visualSettings.mode,
          screen: requirements.screen ? screenVideo : null,
          camera: requirements.camera ? cameraVideo : null,
          mirrorCamera: visualSettings.mirrorCamera,
          pipPosition: visualSettings.pipPosition,
          pipSize: visualSettings.pipSize,
          longEdge: outputLongEdge
        })
        if (!drawn) return

        const signature = sampleCanvasChangeSignature(canvas, signatureCanvas)
        const changeScore = grayscaleMeanAbsoluteDifference(previousSignature, signature)
        const encoded = await compressCompositeCanvas(
          canvas,
          profile,
          framePolicy.frameQuality
        )
        if (runRef.current !== runId || sessionStatusRef.current !== 'running') return
        previousSignature = signature
        const sequence = frameSequenceRef.current + 1
        frameSequenceRef.current = sequence
        const frame: VisualFrame = {
          frameId: `visual-${Date.now()}-${sequence}`,
          capturedAt: Date.now(),
          width: encoded.width,
          height: encoded.height,
          mode: visualSettings.mode,
          bytes: encoded.blob.size,
          overTarget: encoded.overTarget,
          changeScore,
          blob: encoded.blob
        }
        pendingFramesRef.current.push(frame)
        setLastFrameBytes(frame.bytes)
        setLastFrameOverTarget(frame.overTarget)
      } catch {
        if (runRef.current === runId) setStatus('compression-failed')
      } finally {
        if (sampleBusyRef.current === runId) sampleBusyRef.current = null
      }
    }

    const flushBatch = async (): Promise<void> => {
      if (batchBusyRef.current !== null || pendingFramesRef.current.length === 0) return
      batchBusyRef.current = runId
      const pending = pendingFramesRef.current
      pendingFramesRef.current = []
      if (runRef.current !== runId) {
        releaseVisualFrames(pending)
        batchBusyRef.current = null
        return
      }

      const createdAt = Date.now()
      try {
        const result = await deliverAndReleaseVisualBatch(
          batchSinkRef.current,
          {
            batchId: `visual-batch-${createdAt}`,
            createdAt,
            frames: pending
          },
          batchAbortController.signal
        )
        if (runRef.current === runId) {
          setLastBatchAt(createdAt)
          setStatus(result === 'accepted' ? 'ready' : 'waiting-backend')
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return
        if (runRef.current === runId) setStatus('waiting-backend')
      } finally {
        if (batchBusyRef.current === runId) batchBusyRef.current = null
      }
    }

    void sampleFrame()
    const sampleTimer = window.setInterval(
      () => void sampleFrame(),
      visualSettings.sampleIntervalMs
    )
    const batchTimer = window.setInterval(() => void flushBatch(), 3000)
    return () => {
      window.clearInterval(sampleTimer)
      window.clearInterval(batchTimer)
      batchAbortController.abort()
      if (runRef.current === runId) runRef.current += 1
      if (sampleBusyRef.current === runId) sampleBusyRef.current = null
      if (batchBusyRef.current === runId) batchBusyRef.current = null
      releaseVisualFrames(pendingFramesRef.current)
      pendingFramesRef.current = []
    }
  }, [
    cameraStream,
    cameraStreamRef,
    cameraVideoRef,
    captureStream,
    captureStreamRef,
    deliveryEnabled,
    sessionStatus,
    videoRef,
    visualSettings.compressionPreset,
    framePolicy.frameMaxDimension,
    framePolicy.frameQuality,
    visualSettings.mirrorCamera,
    visualSettings.mode,
    visualSettings.pipPosition,
    visualSettings.pipSize,
    visualSettings.sampleIntervalMs
  ])

  useEffect(
    () => () => {
      releaseVisualFrames(pendingFramesRef.current)
      pendingFramesRef.current = []
    },
    []
  )

  return {
    compositeCanvasRef,
    status,
    lastFrameBytes,
    lastFrameOverTarget,
    lastBatchAt
  }
}
