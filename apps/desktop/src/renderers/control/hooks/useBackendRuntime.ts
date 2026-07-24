import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  BackendConnectionState,
  BackendRuntimeStatus,
  BackendSessionSnapshot
} from '../../../shared/contracts'

export type BackendNotice = {
  title: string
  detail: string
}

export function createDisconnectedBackendStatus(now = Date.now()): BackendRuntimeStatus {
  return {
    connection: 'disconnected',
    providersConfigured: false,
    startupError: null,
    recoverableRuntimeSessionId: null,
    session: {
      sessionId: null,
      state: 'idle',
      startedAtMs: null,
      updatedAtMs: now,
      revision: 0
    }
  }
}

export function getBackendNotice(status: BackendRuntimeStatus | null): BackendNotice | null {
  const connection = status?.connection ?? 'starting'
  if (connection === 'connected') return null
  if (connection === 'failed') {
    return {
      title: '本地服务启动失败',
      detail: status?.startupError ?? '请重试，或检查本地后端文件和日志。'
    }
  }
  if (connection === 'disconnected') {
    return {
      title: '本地服务连接中断',
      detail: '正在自动恢复连接，恢复前不会发送新的音频或画面。'
    }
  }
  if (connection === 'connecting') {
    return {
      title: '正在连接实时管线',
      detail: '本地服务已经启动，正在建立安全连接。'
    }
  }
  return {
    title: '正在启动本地服务',
    detail: '通常只需要几秒，完成后即可配置模型或开始直播。'
  }
}

export function useBackendRuntime() {
  const [status, setStatus] = useState<BackendRuntimeStatus | null>(null)
  const [retrying, setRetrying] = useState(false)
  const retryingRef = useRef(false)

  const applyStatus = useCallback((nextStatus: BackendRuntimeStatus): void => {
    setStatus(nextStatus)
  }, [])

  const applySessionSnapshot = useCallback((session: BackendSessionSnapshot): void => {
    setStatus((current) => (current ? { ...current, session } : current))
  }, [])

  const refresh = useCallback(async (): Promise<BackendRuntimeStatus> => {
    const nextStatus = await window.advx.getBackendStatus()
    applyStatus(nextStatus)
    return nextStatus
  }, [applyStatus])

  useEffect(() => {
    let active = true
    const unsubscribe = window.advx.onBackendStatus((nextStatus) => {
      if (active) applyStatus(nextStatus)
    })

    void window.advx
      .getBackendStatus()
      .then((nextStatus) => {
        if (active) applyStatus(nextStatus)
      })
      .catch(() => {
        if (active) applyStatus(createDisconnectedBackendStatus())
      })

    return () => {
      active = false
      unsubscribe()
    }
  }, [applyStatus])

  const retry = useCallback(async (): Promise<void> => {
    if (retryingRef.current) return
    retryingRef.current = true
    setRetrying(true)
    try {
      applyStatus(await window.advx.restartBackend())
    } catch {
      // Main publishes the actionable startup error through onBackendStatus.
    } finally {
      retryingRef.current = false
      setRetrying(false)
    }
  }, [applyStatus])

  const connection: BackendConnectionState = status?.connection ?? 'starting'
  const notice = useMemo(() => getBackendNotice(status), [status])

  return {
    status,
    connection,
    notice,
    loading: status === null,
    retrying,
    busy: status === null || retrying || connection === 'starting' || connection === 'connecting',
    applyStatus,
    applySessionSnapshot,
    refresh,
    retry
  }
}
