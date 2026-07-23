import type { ModelConfig } from "../shared/contracts";

export function resolveModelConfig(
  input: ModelConfig,
  stored: ModelConfig | null
): ModelConfig {
  const resolved = {
    baseUrl: input.baseUrl.trim(),
    model: input.model.trim(),
    apiKey: input.apiKey.trim() || stored?.apiKey || "",
    asrApiKey: input.asrApiKey.trim() || stored?.asrApiKey || ""
  };

  if (!resolved.baseUrl || !resolved.model || !resolved.apiKey || !resolved.asrApiKey) {
    throw new Error("模型地址、模型名称、模型密钥和语音识别密钥均为必填项。");
  }
  return resolved;
}
