import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  DEFAULT_ASR_BASE_URL,
  DEFAULT_ASR_MODEL
} from '../../../shared/contracts'
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
  if (result.runtimeApplyRequired && result.nextSessionRequired) {
    return '模型配置已安全保存，显式应用运行时配置后切换；ASR 配置将在下一场直播加载'
  }
  if (result.runtimeApplyRequired) {
    return '模型配置已安全保存；当前 Session 保持不变，显式应用运行时配置后切换'
  }
  if (result.nextSessionRequired) {
    return '语音识别配置已安全保存；将在下一场直播加载'
  }
  return result.securelyStored
    ? '模型与语音识别配置已安全保存；开始或恢复直播时将加载供应商'
    : '当前系统无法加密密钥；密钥不会落盘，也无法在重启后恢复供应商'
}

export function canSaveModelConfig(input: {
  baseUrl: string
  model: string
  asrBaseUrl: string
  asrModel: string
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
    input.asrBaseUrl.trim().length > 0 &&
    input.asrModel.trim().length > 0 &&
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
  const [providerProfileId, setProviderProfileId] = useState('default')
  const [model, setModel] = useState('')
  const [viewerModel, setViewerModel] = useState('')
  const [memoryModel, setMemoryModel] = useState('')
  const [visualSummaryModel, setVisualSummaryModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [asrBaseUrl, setAsrBaseUrl] = useState(DEFAULT_ASR_BASE_URL)
  const [asrModel, setAsrModel] = useState(DEFAULT_ASR_MODEL)
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
        if (nextStatus.providerProfileId) setProviderProfileId(nextStatus.providerProfileId)
        if (nextStatus.model) setModel(nextStatus.model)
        setViewerModel(nextStatus.viewerModel ?? '')
        setMemoryModel(nextStatus.memoryModel ?? '')
        setVisualSummaryModel(nextStatus.visualSummaryModel ?? '')
        setAsrBaseUrl(nextStatus.asrBaseUrl ?? DEFAULT_ASR_BASE_URL)
        setAsrModel(nextStatus.asrModel ?? DEFAULT_ASR_MODEL)
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
        asrBaseUrl,
        asrModel,
        apiKey,
        asrApiKey,
        status,
        backendConnection,
        loading,
        saving
      }),
    [
      apiKey,
      asrBaseUrl,
      asrApiKey,
      asrModel,
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
      const result = await window.advx.saveModelConfig({
        baseUrl,
        providerProfileId,
        model,
        viewerModel,
        memoryModel,
        visualSummaryModel,
        apiKey,
        asrBaseUrl,
        asrModel,
        asrApiKey
      })
      setApiKey('')
      setAsrApiKey('')
      setStatus({
        baseUrl: baseUrl.trim(),
        providerProfileId: result.providerProfileId,
        model: model.trim(),
        viewerModel: viewerModel.trim() || null,
        memoryModel: memoryModel.trim() || null,
        visualSummaryModel: visualSummaryModel.trim() || null,
        asrBaseUrl: asrBaseUrl.trim(),
        asrModel: asrModel.trim(),
        modelApiKeyStored: result.securelyStored,
        asrApiKeyStored: result.securelyStored
      })
      setProviderProfileId(result.providerProfileId)
      const backendStatus = await window.advx.getBackendStatus()
      onBackendStatus?.(backendStatus)
      setNotice(getModelConfigNotice(result))
    } catch (error) {
      setNotice(`保存失败：${describeError(error)}`)
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }, [
    apiKey,
    asrBaseUrl,
    asrApiKey,
    asrModel,
    baseUrl,
    canSave,
    memoryModel,
    model,
    onBackendStatus,
    providerProfileId,
    viewerModel,
    visualSummaryModel
  ])

  return {
    baseUrl,
    setBaseUrl,
    providerProfileId,
    setProviderProfileId,
    model,
    setModel,
    viewerModel,
    setViewerModel,
    memoryModel,
    setMemoryModel,
    visualSummaryModel,
    setVisualSummaryModel,
    apiKey,
    setApiKey,
    asrBaseUrl,
    setAsrBaseUrl,
    asrModel,
    setAsrModel,
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
