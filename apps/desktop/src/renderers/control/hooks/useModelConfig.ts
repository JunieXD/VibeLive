import { useCallback, useState } from 'react'

export function useModelConfig() {
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [notice, setNotice] = useState<string | null>(null)

  const save = useCallback(async (): Promise<void> => {
    setNotice(null)
    try {
      const result = await window.advx.saveModelConfig({ baseUrl, model, apiKey })
      setApiKey('')
      setNotice(
        result.securelyStored
          ? '配置已安全保存'
          : '普通配置已保存，当前系统无法加密密钥'
      )
    } catch {
      setNotice('保存失败')
    }
  }, [apiKey, baseUrl, model])

  return {
    baseUrl,
    setBaseUrl,
    model,
    setModel,
    apiKey,
    setApiKey,
    notice,
    save
  }
}
