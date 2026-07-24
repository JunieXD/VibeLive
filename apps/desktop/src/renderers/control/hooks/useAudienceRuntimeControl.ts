import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AudienceWorkspaceState } from '../../../shared/audience'
import type {
  DebugTraceSummary,
  ProviderProbeResult,
  RuntimeQuerySnapshot
} from '../../../shared/backend-client'
import type { SessionStatus } from '../../../shared/session'

const AUTO_APPLY_STORAGE_KEY = 'advx.audience-runtime-auto-apply'

type UseAudienceRuntimeControlOptions = {
  workspace: AudienceWorkspaceState
  persistenceReady: boolean
  sessionId: string | null | undefined
  recoverableSessionId: string | null | undefined
  backendConnected: boolean
  sessionStatus: SessionStatus
  onSystemActivity: (text: string) => void
  savedFingerprint: string | null
}

export function useAudienceRuntimeControl({
  workspace,
  persistenceReady,
  sessionId,
  recoverableSessionId,
  backendConnected,
  sessionStatus,
  onSystemActivity,
  savedFingerprint
}: UseAudienceRuntimeControlOptions) {
  const [runtime, setRuntime] = useState<RuntimeQuerySnapshot | null>(null)
  const [autoApply, setAutoApplyState] = useState(
    () => window.localStorage.getItem(AUTO_APPLY_STORAGE_KEY) === 'true'
  )
  const [applying, setApplying] = useState(false)
  const [rollingBack, setRollingBack] = useState(false)
  const [recovering, setRecovering] = useState(false)
  const [probing, setProbing] = useState(false)
  const [loadingTraces, setLoadingTraces] = useState(false)
  const [probe, setProbe] = useState<ProviderProbeResult | null>(null)
  const [probeError, setProbeError] = useState<string | null>(null)
  const [traces, setTraces] = useState<readonly DebugTraceSummary[]>([])
  const [issue, setIssue] = useState<string | null>(null)
  const workspaceFingerprint = useMemo(() => JSON.stringify(workspace), [workspace])
  const [appliedFingerprint, setAppliedFingerprint] = useState<string | null>(null)
  const operationRef = useRef(false)

  const matchesWorkspace = useCallback(async (
    snapshot: RuntimeQuerySnapshot
  ): Promise<boolean> => {
    const room = snapshot.canonical_runtime_spec.room
    const localHash = await window.advx.getAudienceRuntimeConfigHash(
      workspace,
      snapshot.config_revision,
      {
        roomId: room.room_id,
        displayName: room.display_name,
        revision: room.revision
      }
    )
    return runtimeConfigMatchesLocal(snapshot.config_hash, localHash)
  }, [workspace])

  const refresh = useCallback(async (): Promise<RuntimeQuerySnapshot | null> => {
    if (!sessionId) {
      setRuntime(null)
      return null
    }
    try {
      const next = await window.advx.queryAudienceRuntime(sessionId)
      setRuntime(next)
      setIssue(null)
      return next
    } catch (error) {
      setIssue(describeError(error, '无法读取当前观众运行时。'))
      return null
    }
  }, [sessionId])

  useEffect(() => {
    if (!sessionId || (sessionStatus !== 'running' && sessionStatus !== 'paused')) {
      setRuntime(null)
      setAppliedFingerprint(null)
      return
    }
    let active = true
    void refresh().then((next) => {
      if (!next || !active) return
      setAppliedFingerprint(null)
      void matchesWorkspace(next)
        .then((matches) => {
          if (active) setAppliedFingerprint(matches ? workspaceFingerprint : null)
        })
        .catch(() => undefined)
    })
    return () => {
      active = false
    }
  }, [matchesWorkspace, refresh, sessionId, sessionStatus, workspaceFingerprint])

  const apply = useCallback(async (): Promise<void> => {
    if (!sessionId || !runtime || operationRef.current || !persistenceReady) return
    operationRef.current = true
    setApplying(true)
    setIssue(null)
    try {
      const result = await window.advx.applyAudienceRuntime(
        sessionId,
        workspace,
        runtime.config_revision
      )
      setAppliedFingerprint(workspaceFingerprint)
      const next = await refresh()
      onSystemActivity(
        `观众运行时已应用：revision ${result.config_revision}，epoch ${result.audience_epoch}。`
      )
      if (!next) {
        setRuntime((current) => current && {
          ...current,
          config_revision: result.config_revision,
          config_hash: result.config_hash,
          audience_epoch: result.audience_epoch
        })
      }
    } catch (error) {
      const message = describeError(error, '观众运行时应用失败，当前版本保持不变。')
      setIssue(message)
      onSystemActivity(message)
    } finally {
      operationRef.current = false
      setApplying(false)
    }
  }, [
    onSystemActivity,
    persistenceReady,
    refresh,
    runtime,
    sessionId,
    workspace,
    workspaceFingerprint
  ])

  const rollback = useCallback(async (): Promise<void> => {
    if (!sessionId || !runtime || runtime.config_revision <= 1 || operationRef.current) return
    operationRef.current = true
    setRollingBack(true)
    setIssue(null)
    try {
      const result = await window.advx.rollbackAudienceRuntime(
        sessionId,
        runtime.config_revision,
        runtime.config_revision - 1
      )
      setAppliedFingerprint(null)
      await refresh()
      onSystemActivity(
        `观众运行时已回滚：revision ${result.config_revision}，epoch ${result.audience_epoch}。`
      )
    } catch (error) {
      const message = describeError(error, '观众运行时回滚失败。')
      setIssue(message)
      onSystemActivity(message)
    } finally {
      operationRef.current = false
      setRollingBack(false)
    }
  }, [onSystemActivity, refresh, runtime, sessionId])

  const recover = useCallback(async (): Promise<void> => {
    if (!recoverableSessionId || !backendConnected || operationRef.current) return
    operationRef.current = true
    setRecovering(true)
    setIssue(null)
    try {
      const result = await window.advx.recoverAudienceRuntime(recoverableSessionId)
      setRuntime(result)
      const matches = await matchesWorkspace(result).catch(() => false)
      setAppliedFingerprint(matches ? workspaceFingerprint : null)
      onSystemActivity(
        `观众运行时已恢复：session ${result.session_id}，epoch ${result.audience_epoch}。`
      )
    } catch (error) {
      const message = describeError(error, '观众运行时恢复失败，未重放实时输入。')
      setIssue(message)
      onSystemActivity(message)
    } finally {
      operationRef.current = false
      setRecovering(false)
    }
  }, [
    backendConnected,
    matchesWorkspace,
    onSystemActivity,
    recoverableSessionId,
    workspaceFingerprint
  ])

  const runProbe = useCallback(async (): Promise<void> => {
    if (probing) return
    setProbing(true)
    setProbe(null)
    setProbeError(null)
    setIssue(null)
    try {
      const result = await window.advx.probeAudienceProvider()
      setProbe(result)
      onSystemActivity(`供应商能力检测：${result.status}。`)
    } catch (error) {
      const message = describeError(error, '供应商能力检测失败。')
      setProbeError(message)
      setIssue(message)
      onSystemActivity(message)
    } finally {
      setProbing(false)
    }
  }, [onSystemActivity, probing])

  useEffect(() => {
    if (sessionStatus !== 'idle') return
    setProbe(null)
    setProbeError(null)
  }, [sessionStatus])

  const loadTraces = useCallback(async (): Promise<void> => {
    if (!sessionId || loadingTraces) return
    setLoadingTraces(true)
    setIssue(null)
    try {
      const result = await window.advx.queryDebugTraces(sessionId)
      setTraces(result.items)
    } catch (error) {
      setIssue(describeError(error, 'Debug trace 查询失败。'))
    } finally {
      setLoadingTraces(false)
    }
  }, [loadingTraces, sessionId])

  const setAutoApply = useCallback((enabled: boolean): void => {
    setAutoApplyState(enabled)
    window.localStorage.setItem(AUTO_APPLY_STORAGE_KEY, String(enabled))
  }, [])

  const pending = appliedFingerprint !== workspaceFingerprint
  useEffect(() => {
    if (!autoApply || !pending || !persistenceReady || savedFingerprint !== workspaceFingerprint ||
      sessionStatus !== 'running' || !runtime) {
      return
    }
    const timer = window.setTimeout(() => void apply(), 850)
    return () => window.clearTimeout(timer)
  }, [
    apply,
    autoApply,
    pending,
    persistenceReady,
    runtime,
    savedFingerprint,
    sessionStatus,
    workspaceFingerprint
  ])

  return {
    runtime,
    autoApply,
    applying,
    rollingBack,
    recovering,
    probing,
    loadingTraces,
    probe,
    probeError,
    traces,
    issue,
    pending,
    canApply: Boolean(
      runtime &&
      sessionStatus === 'running' &&
      persistenceReady &&
      savedFingerprint === workspaceFingerprint
    ),
    setAutoApply,
    apply,
    rollback,
    recover,
    runProbe,
    loadTraces,
    refresh
  }
}

function describeError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

export function runtimeConfigMatchesLocal(
  backendConfigHash: string,
  localConfigHash: string
): boolean {
  return backendConfigHash.length === 64 && backendConfigHash === localConfigHash
}
