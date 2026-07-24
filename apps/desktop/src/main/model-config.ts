import type {
  ModelConfig,
  RuntimeModelProviderCandidate
} from "../shared/contracts";

export type ResolvedModelProvider = {
  providerProfileId: string;
  baseUrl: string;
  defaultModel: string;
  viewerModel: string;
  memoryModel: string;
  visualSummaryModel: string;
  apiKey: string;
};

export type RuntimeProviderIdentity = {
  provider_profile_id: string;
  viewer_model: string;
  memory_model: string;
  visual_summary_model: string;
};

export function resolveModelConfig(
  input: ModelConfig,
  stored: ModelConfig | null
): ModelConfig {
  const resolved = {
    baseUrl: input.baseUrl.trim(),
    providerProfileId: input.providerProfileId.trim() || "default",
    model: input.model.trim(),
    viewerModel: input.viewerModel.trim(),
    memoryModel: input.memoryModel.trim(),
    visualSummaryModel: input.visualSummaryModel.trim(),
    apiKey: input.apiKey.trim() || stored?.apiKey || "",
    asrApiKey: input.asrApiKey.trim() || stored?.asrApiKey || ""
  };

  if (!resolved.baseUrl || !resolved.model || !resolved.apiKey || !resolved.asrApiKey) {
    throw new Error("模型地址、模型名称、模型密钥和语音识别密钥均为必填项。");
  }
  return resolved;
}

export function reviseProviderProfileForActiveSession(
  config: ModelConfig,
  stored: ModelConfig | null,
  sessionActive: boolean,
  revisionId: string
): ModelConfig {
  if (
    !sessionActive ||
    stored === null ||
    config.providerProfileId !== stored.providerProfileId ||
    (config.baseUrl === stored.baseUrl && config.apiKey === stored.apiKey)
  ) {
    return config;
  }
  const suffix = `-rev-${revisionId.replace(/[^a-zA-Z0-9]/g, "").slice(0, 8)}`;
  const readablePrefix = config.providerProfileId
    .replace(/-rev-[a-zA-Z0-9]{1,8}$/, "")
    .slice(0, 128 - suffix.length);
  return {
    ...config,
    providerProfileId: `${readablePrefix || "default"}${suffix}`.slice(0, 128)
  };
}

export function modelProviderChanged(
  config: ModelConfig,
  stored: ModelConfig | null
): boolean {
  return (
    stored === null ||
    config.baseUrl !== stored.baseUrl ||
    config.providerProfileId !== stored.providerProfileId ||
    config.model !== stored.model ||
    config.viewerModel !== stored.viewerModel ||
    config.memoryModel !== stored.memoryModel ||
    config.visualSummaryModel !== stored.visualSummaryModel ||
    config.apiKey !== stored.apiKey
  );
}

export type ProviderSessionConfigurator = {
  configureProviders(config: ModelConfig): Promise<void>;
};

export function isProviderPipelineAlreadyConfigured(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "providers_already_configured"
  );
}

export async function configureProviderForSession(
  config: ModelConfig,
  backendClient: ProviderSessionConfigurator,
  restartBackend: () => Promise<unknown>
): Promise<void> {
  try {
    await backendClient.configureProviders(config);
  } catch (error) {
    if (!isProviderPipelineAlreadyConfigured(error)) throw error;
    await restartBackend();
    await backendClient.configureProviders(config);
  }
}

export function resolveModelProvider(config: ModelConfig): ResolvedModelProvider {
  const defaultModel = config.model.trim();
  return {
    providerProfileId: config.providerProfileId.trim() || "default",
    baseUrl: config.baseUrl.trim(),
    defaultModel,
    viewerModel: config.viewerModel.trim() || defaultModel,
    memoryModel: config.memoryModel.trim() || defaultModel,
    visualSummaryModel: config.visualSummaryModel.trim() || defaultModel,
    apiKey: config.apiKey
  };
}

export function createRuntimeProviderCandidate(
  config: ModelConfig
): RuntimeModelProviderCandidate {
  const provider = resolveModelProvider(config);
  return {
    provider_profile_id: provider.providerProfileId,
    model_base_url: provider.baseUrl,
    model_name: provider.defaultModel,
    viewer_model: provider.viewerModel,
    memory_model: provider.memoryModel,
    visual_summary_model: provider.visualSummaryModel,
    model_api_key: provider.apiKey
  };
}

export function mergeProviderProfileSnapshots(
  snapshots: readonly ModelConfig[],
  config: ModelConfig
): ModelConfig[] {
  if (snapshots.some((snapshot) => JSON.stringify(snapshot) === JSON.stringify(config))) {
    return [...snapshots];
  }
  return [...snapshots, config];
}

export function selectRuntimeProviderConfig(
  snapshots: readonly ModelConfig[],
  target: RuntimeProviderIdentity
): ModelConfig {
  const matches = snapshots.filter((snapshot) => {
    const provider = resolveModelProvider(snapshot);
    return (
      provider.providerProfileId === target.provider_profile_id &&
      provider.viewerModel === target.viewer_model &&
      provider.memoryModel === target.memory_model &&
      provider.visualSummaryModel === target.visual_summary_model
    );
  });
  if (matches.length !== 1) {
    throw new Error(
      matches.length === 0
        ? `缺少供应商 ${target.provider_profile_id} 的安全凭据快照，已阻止运行时切换。`
        : `供应商 ${target.provider_profile_id} 存在多个凭据快照，无法安全判定，已阻止运行时切换。`
    );
  }
  return matches[0];
}
