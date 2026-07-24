import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction
} from 'react'
import type { AudienceWorkspaceState } from '../../../shared/audience'
import type { RuntimeViewer } from '../../../shared/backend-client'
import type {
  BackendBarrageEvent,
  BarrageEvent,
  BarrageMode
} from '../../../shared/contracts'
import type { SessionStatus } from '../../../shared/session'
import type { ActivityItem } from './useActivityFeed'
import {
  insertSelectedTarget,
  parseTargetedMessage,
  selectionMatches,
  suggestMentionTargets,
  updateSelectedTarget,
  buildRuntimeMessageTargets,
  type MessageTarget,
  type SelectedMessageTarget
} from '../features/live/message-target'

const BARRAGE_PREVIEW_TEXT: Record<BarrageMode, string> = {
  scroll: '滚动弹幕预览',
  top: '顶端弹幕预览',
  bottom: '底端弹幕预览'
}

type UseAudienceBarrageOptions = {
  workspace: AudienceWorkspaceState
  runtimeViewers: readonly RuntimeViewer[]
  sessionStatus: SessionStatus
  audienceSessionActive: boolean
  overlayVisible: boolean
  showOverlay: () => Promise<void>
  message: string
  setMessage: Dispatch<SetStateAction<string>>
  appendAudienceActivity: (item: Omit<ActivityItem, 'source'>) => void
  appendUserActivity: (text: string) => void
  appendSystemActivity?: (text: string) => void
  clearAudienceActivity: () => void
}

export function useAudienceBarrage({
  workspace,
  runtimeViewers,
  sessionStatus,
  audienceSessionActive,
  overlayVisible,
  showOverlay,
  message,
  setMessage,
  appendAudienceActivity,
  appendUserActivity,
  appendSystemActivity,
  clearAudienceActivity
}: UseAudienceBarrageOptions) {
  const [barrageTotal, setBarrageTotal] = useState(0)
  const [messageSending, setMessageSending] = useState(false)
  const [messageError, setMessageError] = useState<string | null>(null)
  const [selectedMessageTarget, setSelectedMessageTarget] =
    useState<SelectedMessageTarget | null>(null)
  const acceptedBarrageAfterRef = useRef(0)
  const activeAudience = useMemo(
    () => runtimeViewers.filter((viewer) => viewer.lifecycle_state === 'active'),
    [runtimeViewers]
  )
  const messageTargets = useMemo<readonly MessageTarget[]>(
    () => buildRuntimeMessageTargets(runtimeViewers, workspace.personas),
    [runtimeViewers, workspace.personas]
  )
  const targetSuggestions = useMemo(
    () => selectedMessageTarget && selectionMatches(message, selectedMessageTarget)
      ? []
      : suggestMentionTargets(message, messageTargets),
    [message, messageTargets, selectedMessageTarget]
  )

  useEffect(() => {
    if (sessionStatus === 'running') acceptedBarrageAfterRef.current = Date.now()
  }, [sessionStatus])

  useEffect(() => window.advx.onBackendBarrage((backendEvent: BackendBarrageEvent) => {
    if (!audienceSessionActive) return
    if (!shouldAcceptBackendBarrage(
      sessionStatus,
      backendEvent.createdAt,
      acceptedBarrageAfterRef.current
    )) return
    const base = workspace.personas.find((persona) => persona.id === backendEvent.personaId)
    const event: BarrageEvent = {
      ...backendEvent,
      audienceName: backendEvent.audienceName,
      color: base?.color ?? '#5f8f7a',
      mode: 'scroll'
    }
    setBarrageTotal((current) => current + 1)
    appendAudienceActivity({
      id: event.barrageId,
      author: backendEvent.audienceName,
      text: event.text,
      color: event.color
    })
    if (overlayVisible) void window.advx.pushBarrage(event)
  }), [
    appendAudienceActivity,
    audienceSessionActive,
    overlayVisible,
    sessionStatus,
    workspace.personas
  ])

  const previewBarrage = useCallback(async (mode: BarrageMode): Promise<void> => {
    await showOverlay()
    await window.advx.pushBarrage({
      barrageId: `preview-${crypto.randomUUID()}`,
      audienceId: 'preview',
      audienceName: '预览',
      text: BARRAGE_PREVIEW_TEXT[mode],
      color: '#5f8f7a',
      createdAt: Date.now(),
      mode
    })
  }, [showOverlay])

  const clearBarrage = useCallback(async (): Promise<void> => {
    clearAudienceActivity()
    await window.advx.clearOverlay()
  }, [clearAudienceActivity])

  const sendUserMessage = useCallback(async (): Promise<void> => {
    const targeted = parseTargetedMessage(message, selectedMessageTarget)
    if (!targeted.text || messageSending || sessionStatus !== 'running' || !audienceSessionActive) {
      return
    }
    setMessageSending(true)
    setMessageError(null)
    appendUserActivity(message.trim())
    setMessage('')
    try {
      await window.advx.submitUserText(targeted.text, {
        targetViewerId: targeted.targetViewerId,
        targetPersonaId: targeted.targetPersonaId
      })
      setSelectedMessageTarget(null)
    } catch (error) {
      const errorMessage = `文字未送达后端：${
        error instanceof Error && error.message ? error.message : '实时连接异常。'
      }`
      setMessage((current) => current || message.trim())
      setMessageError(errorMessage)
      appendSystemActivity?.(errorMessage)
    } finally {
      setMessageSending(false)
    }
  }, [
    appendSystemActivity,
    appendUserActivity,
    audienceSessionActive,
    message,
    messageSending,
    selectedMessageTarget,
    sessionStatus,
    setMessage
  ])

  return {
    activeAudience,
    barrageTotal,
    previewBarrage,
    clearBarrage,
    sendUserMessage,
    messageSending,
    messageError,
    targetSuggestions,
    changeMessage: (value: string) => {
      setSelectedMessageTarget((current) =>
        current ? updateSelectedTarget(message, value, current) : null
      )
      setMessage(value)
    },
    selectMessageTarget: (target: MessageTarget) => {
      const next = insertSelectedTarget(message, target)
      setSelectedMessageTarget(next.selection)
      setMessage(next.message)
    }
  }
}

export function shouldAcceptBackendBarrage(
  sessionStatus: SessionStatus,
  createdAt = Number.POSITIVE_INFINITY,
  acceptedAfter = 0
): boolean {
  return sessionStatus === 'running' && createdAt >= acceptedAfter
}
