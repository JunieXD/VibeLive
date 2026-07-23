import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import {
  autoIngestMeme,
  compileAudienceWorkspaceSnapshot,
  recordMemeUsage,
  type AudienceWorkspaceState,
  type MemeCandidate,
  type RuntimePersona
} from '../../../shared/audience'
import type {
  BackendBarrageEvent,
  BarrageEvent,
  BarrageMode
} from '../../../shared/contracts'
import { demoLines } from '../../../shared/demo'
import type { SessionStatus } from '../../../shared/session'
import type { ActivityItem } from './useActivityFeed'

const DEMO_BARRAGE_MODES: readonly BarrageMode[] = [
  'scroll',
  'scroll',
  'top',
  'scroll',
  'scroll',
  'bottom'
]

const BARRAGE_PREVIEW_TEXT: Record<BarrageMode, string> = {
  scroll: '这是一条滚动弹幕',
  top: '这是一条顶端固定弹幕',
  bottom: '这是一条底端固定弹幕'
}

const BACKEND_AUDIENCE_PRESENTATION: Record<string, { name: string; color: string }> = {
  'builtin-luna': { name: 'Luna', color: '#e879a9' },
  'builtin-max': { name: 'Max', color: '#3da9d5' },
  'builtin-nova': { name: 'Nova', color: '#8f7bd8' }
}

function backendAudienceFor(
  audienceId: string,
  personas: readonly RuntimePersona[]
): { name: string; color: string } {
  const localPersona = personas.find((persona) => persona.id === audienceId)
  if (localPersona) return { name: localPersona.name, color: localPersona.color }
  return (
    BACKEND_AUDIENCE_PRESENTATION[audienceId] ?? {
      name: `AI 观众 ${audienceId.slice(-6)}`,
      color: '#5f8f7a'
    }
  )
}

function describeBackendError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

function selectWeightedPersona(
  personas: readonly RuntimePersona[],
  sequence: number
): RuntimePersona | undefined {
  const totalWeight = personas.reduce((total, persona) => total + persona.weight, 0)
  if (totalWeight <= 0) return personas[0]
  let cursor = (sequence * 7) % totalWeight
  for (const persona of personas) {
    cursor -= persona.weight
    if (cursor < 0) return persona
  }
  return personas.at(-1)
}

function proposeDemoMemeCandidate(input: {
  modeId: string
  text: string
  sourceKinds: MemeCandidate['sourceKinds']
  evidenceSummary: string
  personaTags?: readonly string[]
}): MemeCandidate | null {
  const text = input.text.trim()
  const looksLikeRoomMeme =
    /(这下|有说法|绷|笑死|离谱|稳了|好家伙|来了|寄了?|赢了?|输了?|典|急了?|孝|乐|麻了?|草|牛|神|逆天|抽象|懂不懂|[?？!！]{2,}|(.)\1{2,})/u.test(
      text
    )
  if (
    text.length < 2 ||
    text.length > 60 ||
    !looksLikeRoomMeme ||
    /(?:1[3-9]\d{9}|[\w.+-]+@[\w.-]+\.[a-z]{2,})/i.test(text)
  ) {
    return null
  }
  return {
    id: `meme-${crypto.randomUUID()}`,
    modeId: input.modeId,
    text,
    personaTags: input.personaTags,
    sourceKinds: input.sourceKinds,
    evidenceSummary: input.evidenceSummary.slice(0, 160),
    createdAt: new Date().toISOString()
  }
}

type UseDemoBarrageOptions = {
  workspace: AudienceWorkspaceState
  setWorkspace: Dispatch<SetStateAction<AudienceWorkspaceState>>
  sessionStatus: SessionStatus
  overlayVisible: boolean
  showOverlay: () => Promise<void>
  message: string
  setMessage: Dispatch<SetStateAction<string>>
  appendAudienceActivity: (item: Omit<ActivityItem, 'source'>) => void
  appendUserActivity: (text: string) => void
  appendSystemActivity?: (text: string) => void
  clearAudienceActivity: () => void
  backendConnected?: boolean
}

export function useDemoBarrage({
  workspace,
  setWorkspace,
  sessionStatus,
  overlayVisible,
  showOverlay,
  message,
  setMessage,
  appendAudienceActivity,
  appendUserActivity,
  appendSystemActivity,
  backendConnected = true,
  clearAudienceActivity
}: UseDemoBarrageOptions) {
  const [barrageTotal, setBarrageTotal] = useState(0)
  const [messageSending, setMessageSending] = useState(false)
  const [messageError, setMessageError] = useState<string | null>(null)
  const sequenceRef = useRef(0)
  const runtime = useMemo(() => compileAudienceWorkspaceSnapshot(workspace), [workspace])
  const activeAudience = runtime.personas

  const acceptDirectorMemeCandidate = useCallback(
    (candidate: MemeCandidate): void => {
      setWorkspace((current) => {
        if (!current.modeState.modes.some((mode) => mode.id === candidate.modeId)) return current
        const result = autoIngestMeme(current.memes, candidate)
        return result.accepted ? { ...current, memes: result.entries } : current
      })
    },
    [setWorkspace]
  )

  const emitBarrage = useCallback(
    (text?: string, requestedMode?: BarrageMode, forceOverlay = false): void => {
      const sequence = sequenceRef.current
      const member = selectWeightedPersona(activeAudience, sequence)
      if (!member) return
      const mode =
        requestedMode ??
        (text === undefined
          ? DEMO_BARRAGE_MODES[sequence % DEMO_BARRAGE_MODES.length]
          : 'scroll')
      const learnedMeme =
        text === undefined && runtime.memes.length > 0 && sequence % 3 === 2
          ? runtime.memes[sequence % runtime.memes.length]
          : undefined

      const event: BarrageEvent = {
        barrageId: `demo-${Date.now()}-${sequence}`,
        audienceId: member.id,
        audienceName: member.name,
        text: text ?? learnedMeme?.text ?? demoLines[sequence % demoLines.length],
        color: member.color,
        createdAt: Date.now(),
        mode
      }
      sequenceRef.current += 1
      setBarrageTotal((current) => current + 1)
      appendAudienceActivity({
        id: event.barrageId,
        author: member.name,
        text: event.text,
        color: member.color
      })

      if (overlayVisible || forceOverlay) void window.advx.pushBarrage(event)

      if (learnedMeme) {
        setWorkspace((current) => {
          if (!current.memes.some((meme) => meme.id === learnedMeme.id)) return current
          return {
            ...current,
            memes: recordMemeUsage(current.memes, learnedMeme.id, new Date().toISOString())
          }
        })
      } else if (sequence > 0 && sequence % 4 === 0) {
        const candidate = proposeDemoMemeCandidate({
          modeId: runtime.mode.id,
          text: event.text,
          sourceKinds: ['audience_barrage'],
          evidenceSummary: `${member.name} 在直播互动中形成的房间短句`,
          personaTags: [member.id]
        })
        if (candidate) acceptDirectorMemeCandidate(candidate)
      }
    },
    [
      acceptDirectorMemeCandidate,
      activeAudience,
      appendAudienceActivity,
      overlayVisible,
      runtime,
      setWorkspace
    ]
  )

  useEffect(() => {
    if (sessionStatus !== 'running' || backendConnected) return
    const timer = window.setInterval(() => emitBarrage(), 5200)
    return () => window.clearInterval(timer)
  }, [backendConnected, emitBarrage, sessionStatus])

  useEffect(() => {
    return window.advx.onBackendBarrage((backendEvent: BackendBarrageEvent) => {
      const member = backendAudienceFor(backendEvent.audienceId, activeAudience)
      const event: BarrageEvent = {
        ...backendEvent,
        audienceName: member.name,
        color: member.color,
        mode: 'scroll'
      }
      setBarrageTotal((current) => current + 1)
      appendAudienceActivity({
        id: event.barrageId,
        author: member.name,
        text: event.text,
        color: member.color
      })
      if (overlayVisible) void window.advx.pushBarrage(event)

      const candidate = proposeDemoMemeCandidate({
        modeId: runtime.mode.id,
        text: event.text,
        sourceKinds: ['audience_barrage'],
        evidenceSummary: `${member.name} 在真实直播互动中形成的房间短句`,
        personaTags: [event.audienceId]
      })
      if (candidate) acceptDirectorMemeCandidate(candidate)
    })
  }, [
    acceptDirectorMemeCandidate,
    activeAudience,
    appendAudienceActivity,
    overlayVisible,
    runtime.mode.id
  ])

  const previewBarrage = useCallback(
    async (mode: BarrageMode): Promise<void> => {
      await showOverlay()
      emitBarrage(BARRAGE_PREVIEW_TEXT[mode], mode, true)
    },
    [emitBarrage, showOverlay]
  )

  const clearBarrage = useCallback(async (): Promise<void> => {
    clearAudienceActivity()
    await window.advx.clearOverlay()
  }, [clearAudienceActivity])

  const sendUserMessage = useCallback(async (): Promise<void> => {
    const trimmed = message.trim()
    if (!trimmed || messageSending || sessionStatus !== 'running') return
    setMessageSending(true)
    setMessageError(null)
    appendUserActivity(trimmed)
    setMessage('')
    const candidate = proposeDemoMemeCandidate({
      modeId: runtime.mode.id,
      text: trimmed,
      sourceKinds: ['user_text'],
      evidenceSummary: `用户在房间文字中主动说出：“${trimmed.slice(0, 72)}”`
    })
    if (candidate) acceptDirectorMemeCandidate(candidate)
    try {
      await window.advx.submitUserText(trimmed)
    } catch (error) {
      const errorMessage = `文字未送达后端：${describeBackendError(error, '实时连接异常。')}`
      setMessage((current) => current || trimmed)
      setMessageError(errorMessage)
      appendSystemActivity?.(errorMessage)
    } finally {
      setMessageSending(false)
    }
  }, [
    acceptDirectorMemeCandidate,
    appendSystemActivity,
    appendUserActivity,
    message,
    messageSending,
    runtime.mode.id,
    sessionStatus,
    setMessage
  ])

  return {
    runtime,
    activeAudience,
    barrageTotal,
    emitBarrage,
    previewBarrage,
    clearBarrage,
    sendUserMessage,
    messageSending,
    messageError
  }
}
