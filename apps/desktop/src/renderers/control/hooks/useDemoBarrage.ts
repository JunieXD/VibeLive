import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import {
  autoIngestMeme,
  compileAudienceWorkspaceSnapshot,
  recordMemeUsage,
  type AudienceWorkspaceState,
  type MemeCandidate,
  type RuntimePersona
} from '../../../shared/audience'
import type { BarrageEvent, BarrageMode } from '../../../shared/contracts'
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
  clearAudienceActivity: () => void
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
  clearAudienceActivity
}: UseDemoBarrageOptions) {
  const [barrageTotal, setBarrageTotal] = useState(0)
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
        text: text ?? learnedMeme?.text ?? demoLines[sequence % demoLines.length],
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
    if (sessionStatus !== 'running') return
    const timer = window.setInterval(() => emitBarrage(), 5200)
    return () => window.clearInterval(timer)
  }, [emitBarrage, sessionStatus])

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

  const sendUserMessage = useCallback((): void => {
    const trimmed = message.trim()
    if (!trimmed) return
    appendUserActivity(trimmed)
    setMessage('')
    const candidate = proposeDemoMemeCandidate({
      modeId: runtime.mode.id,
      text: trimmed,
      sourceKinds: ['user_text'],
      evidenceSummary: `用户在房间文字中主动说出：“${trimmed.slice(0, 72)}”`
    })
    if (candidate) acceptDirectorMemeCandidate(candidate)
    if (sessionStatus === 'running') {
      window.setTimeout(
        () => emitBarrage(`听到了。关于“${trimmed.slice(0, 20)}”，我想再看一会儿。`),
        550
      )
    }
  }, [
    acceptDirectorMemeCandidate,
    appendUserActivity,
    emitBarrage,
    message,
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
    sendUserMessage
  }
}
