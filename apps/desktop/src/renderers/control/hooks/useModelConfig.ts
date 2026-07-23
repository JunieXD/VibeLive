import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  BackendConnectionState,
  BackendRuntimeStatus,
  ModelConfigStatus,
  SaveModelConfigResult
} from '../../../shared/contracts'

type UseModelConfigOptions = {
  backendConnection?: BackendConnectionState
  onBackendStatus?: (status: BackendRuntimeStatus) => void
}

export function getModelConfigNotice(result: SaveModelConfigResult): string {
  if (result.restartRequired) {
    return '配置已保存；后端已使用另一组配置，请重启桌面应用后生效'
  }
  return result.securelyStored
    ? '模型与语音识别配置已安全保存并接入后端'
    : '配置已接入本次运行；当前系统无法加密密钥，因此密钥不会落盘'
}

export function canSaveModelConfig(input: {
  baseUrl: string
  model: string
  apiKey: string
  asrApiKey: string
  status: ModelConfigStatus | null
  backendConnection?: BackendConnectionState
  loading: boolean
  saving: boolean
}): boolean {
  return (
    !input.loading &&
    !input.saving &&
    input.backendConnection !== 'starting' &&
    input.backendConnection !== 'connecting' &&
    input.backendConnection !== 'disconnected' &&
    input.backendConnection !== 'failed' &&
    input.baseUrl.trim().length > 0 &&
    input.model.trim().length > 0 &&
    (input.apiKey.trim().length > 0 || input.status?.modelApiKeyStored === true) &&
    (input.asrApiKey.trim().length > 0 || input.status?.asrApiKeyStored === true)
  )
}

function describeError(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '请检查后端连接和配置内容。'
}

export function useModelConfig(options: UseModelConfigOptions = {}) {
  const { backendConnection, onBackendStatus } = options
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [asrApiKey, setAsrApiKey] = useState('')
  const [status, setStatus] = useState<ModelConfigStatus | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)

  useEffect(() => {
    let active = true
    void window.advx
      .getModelConfigStatus()
      .then((nextStatus) => {
        if (!active) return
        setStatus(nextStatus)
        if (nextStatus.baseUrl) setBaseUrl(nextStatus.baseUrl)
        if (nextStatus.model) setModel(nextStatus.model)
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const canSave = useMemo(
    () =>
      canSaveModelConfig({
        baseUrl,
        model,
        apiKey,
        asrApiKey,
        status,
        backendConnection,
        loading,
        saving
      }),
    [
      apiKey,
      asrApiKey,
      baseUrl,
      loading,
      model,
      backendConnection,
      saving,
      status?.asrApiKeyStored,
      status?.modelApiKeyStored
    ]
  )

  const save = useCallback(async (): Promise<void> => {
    if (savingRef.current || !canSave) return
    savingRef.current = true
    setSaving(true)
    setNotice(null)
    try {
      const result = await window.advx.saveModelConfig({ baseUrl, model, apiKey, asrApiKey })
      setApiKey('')
      setAsrApiKey('')
      setStatus({
        baseUrl: baseUrl.trim(),
        model: model.trim(),
        modelApiKeyStored: result.securelyStored,
        asrApiKeyStored: result.securelyStored
      })
      const backendStatus = await window.advx.getBackendStatus()
      onBackendStatus?.(backendStatus)
      setNotice(getModelConfigNotice(result))
    } catch (error) {
      setNotice(`保存失败：${describeError(error)}`)
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }, [apiKey, asrApiKey, baseUrl, canSave, model, onBackendStatus])

  return {
    baseUrl,
    setBaseUrl,
    model,
    setModel,
    apiKey,
    setApiKey,
    asrApiKey,
    setAsrApiKey,
    status,
    notice,
    loading,
    saving,
    busy: loading || saving,
    canSave,
    save
  }
}
