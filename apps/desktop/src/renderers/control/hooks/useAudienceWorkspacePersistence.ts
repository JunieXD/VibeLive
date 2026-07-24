import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction
} from 'react'
import {
  createInitialAudienceWorkspace,
  type AudienceWorkspaceState
} from '../../../shared/audience'
import type { SaveAudienceWorkspaceResult } from '../../../shared/contracts'

type UseAudienceWorkspacePersistenceOptions = {
  onSystemActivity: (text: string) => void
}

type AudienceWorkspacePersistence = {
  workspace: AudienceWorkspaceState
  setWorkspace: Dispatch<SetStateAction<AudienceWorkspaceState>>
  ready: boolean
  loadError: string | null
  reset: () => void
  retry: () => Promise<void>
  documentsNeedSync: boolean
  savedFingerprint: string | null
}

export function useAudienceWorkspacePersistence({
  onSystemActivity
}: UseAudienceWorkspacePersistenceOptions): AudienceWorkspacePersistence {
  const [workspace, setWorkspace] = useState<AudienceWorkspaceState>(
    createInitialAudienceWorkspace
  )
  const [ready, setReady] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [documentsNeedSync, setDocumentsNeedSync] = useState(false)
  const [savedFingerprint, setSavedFingerprint] = useState<string | null>(null)

  const persistenceErrorRef = useRef(false)
  const documentSyncErrorRef = useRef(false)
  const workspaceRef = useRef(workspace)
  const readyRef = useRef(ready)
  const loadRequestRef = useRef(0)
  const onSystemActivityRef = useRef(onSystemActivity)

  workspaceRef.current = workspace
  readyRef.current = ready
  onSystemActivityRef.current = onSystemActivity

  const handleSaveResult = useCallback((
    result: SaveAudienceWorkspaceResult,
    savedWorkspace?: AudienceWorkspaceState
  ): void => {
    persistenceErrorRef.current = false
    if (savedWorkspace) setSavedFingerprint(JSON.stringify(savedWorkspace))
    if (result.personaDocumentsSynced) {
      documentSyncErrorRef.current = false
      setDocumentsNeedSync(false)
      return
    }

    setDocumentsNeedSync(true)
    if (documentSyncErrorRef.current) return
    documentSyncErrorRef.current = true
    onSystemActivityRef.current(
      result.personaDocumentsError
        ? `观众配置已保存，但 personality.md 暂未同步：${result.personaDocumentsError}`
        : '观众配置已保存，但 personality.md 暂未同步。'
    )
  }, [])

  const reportSaveFailure = useCallback((): void => {
    if (persistenceErrorRef.current) return
    persistenceErrorRef.current = true
    onSystemActivityRef.current('模式与人格配置暂未保存，请检查本地数据目录。')
  }, [])

  const retry = useCallback(async (): Promise<void> => {
    const requestId = loadRequestRef.current + 1
    loadRequestRef.current = requestId
    readyRef.current = false
    setReady(false)
    try {
      const storedWorkspace = await window.advx.loadAudienceWorkspace()
      if (loadRequestRef.current !== requestId) return
      const loadedWorkspace = storedWorkspace ?? createInitialAudienceWorkspace()
      workspaceRef.current = loadedWorkspace
      setWorkspace(loadedWorkspace)
      setSavedFingerprint(JSON.stringify(loadedWorkspace))
      setLoadError(null)
      readyRef.current = true
      setReady(true)
    } catch (error) {
      if (loadRequestRef.current !== requestId) return
      const message =
        error instanceof Error ? error.message : '观众配置加载失败，原文件未被覆盖。'
      readyRef.current = false
      setLoadError(message)
      onSystemActivityRef.current(message)
    }
  }, [])

  const reset = useCallback((): void => {
    loadRequestRef.current += 1
    const initialWorkspace = createInitialAudienceWorkspace()
    workspaceRef.current = initialWorkspace
    readyRef.current = true
    setWorkspace(initialWorkspace)
    setSavedFingerprint(null)
    setLoadError(null)
    setReady(true)
    onSystemActivityRef.current(
      '已显式重置观众配置；受保护的拒绝文件仍保留在本地数据目录。'
    )
  }, [])

  useEffect(() => {
    void retry()
    return () => {
      loadRequestRef.current += 1
    }
  }, [retry])

  useEffect(() => {
    if (!ready) return
    const timer = window.setTimeout(() => {
      void window.advx
        .saveAudienceWorkspace(workspace)
        .then((result) => handleSaveResult(result, workspace))
        .catch(reportSaveFailure)
    }, 350)
    return () => window.clearTimeout(timer)
  }, [handleSaveResult, ready, reportSaveFailure, workspace])

  useEffect(() => {
    if (!ready || !documentsNeedSync) return
    const timer = window.setInterval(() => {
      void window.advx
        .saveAudienceWorkspace(workspaceRef.current)
        .then((result) => handleSaveResult(result, workspaceRef.current))
        .catch(reportSaveFailure)
    }, 10_000)
    return () => window.clearInterval(timer)
  }, [documentsNeedSync, handleSaveResult, ready, reportSaveFailure])

  useEffect(
    () =>
      window.advx.onCloseRequested(() => {
        if (!readyRef.current) {
          void window.advx.confirmCloseAfterAudienceSave()
          return
        }
        void window.advx
          .saveAudienceWorkspace(workspaceRef.current)
          .then((result) => handleSaveResult(result, workspaceRef.current))
          .catch(reportSaveFailure)
          .finally(() => window.advx.confirmCloseAfterAudienceSave())
      }),
    [handleSaveResult, reportSaveFailure]
  )

  return {
    workspace,
    setWorkspace,
    ready,
    loadError,
    reset,
    retry,
    documentsNeedSync,
    savedFingerprint
  }
}
