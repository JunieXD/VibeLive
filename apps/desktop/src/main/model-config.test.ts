import { describe, expect, it, vi } from "vitest";
import type { ModelConfig } from "../shared/contracts";
import {
  asrProviderChanged,
  createRuntimeProviderCandidate,
  configureProviderForSession,
  isProviderPipelineAlreadyConfigured,
  mergeProviderProfileSnapshots,
  modelProviderChanged,
  reviseProviderProfileForActiveSession,
  resolveModelConfig,
  resolveModelProvider,
  selectRuntimeProviderConfig
} from "./model-config";

const storedConfig: ModelConfig = {
  baseUrl: "https://stored.example/v1",
  providerProfileId: "stored-profile",
  model: "stored-model",
  viewerModel: "stored-viewer",
  memoryModel: "",
  visualSummaryModel: "",
  apiKey: "stored-model-key",
  asrBaseUrl: "https://speech.example/v1",
  asrModel: "stored-asr-model",
  asrApiKey: "stored-asr-key"
};

describe("model configuration", () => {
  it("retains A and B snapshots so rollback can resolve A without using current B", () => {
    const profileA = {
      ...storedConfig,
      providerProfileId: "profile-a",
      model: "model-a",
      viewerModel: "",
      apiKey: "secret-a"
    };
    const profileB = {
      ...storedConfig,
      providerProfileId: "profile-b",
      model: "model-b",
      viewerModel: "",
      apiKey: "secret-b"
    };
    const snapshots = mergeProviderProfileSnapshots(
      mergeProviderProfileSnapshots([], profileA),
      profileB
    );
    const selected = selectRuntimeProviderConfig(snapshots, {
      provider_profile_id: "profile-a",
      viewer_model: "model-a",
      memory_model: "model-a",
      visual_summary_model: "model-a"
    });
    expect(selected).toEqual(profileA);
    expect(createRuntimeProviderCandidate(selected)).toMatchObject({
      provider_profile_id: "profile-a",
      model_api_key: "secret-a"
    });
  });

  it("blocks rollback when the target provider credential snapshot is missing", () => {
    const profileB = {
      ...storedConfig,
      providerProfileId: "profile-b",
      model: "model-b",
      viewerModel: ""
    };
    expect(() => selectRuntimeProviderConfig([profileB], {
      provider_profile_id: "profile-a",
      viewer_model: "model-a",
      memory_model: "model-a",
      visual_summary_model: "model-a"
    })).toThrow("缺少供应商 profile-a")
  });

  it("reuses safely stored credentials when replacement fields are blank", () => {
    expect(
      resolveModelConfig(
        {
          baseUrl: " https://new.example/v1 ",
          providerProfileId: " profile-a ",
          model: " new-model ",
          viewerModel: "",
          memoryModel: " ",
          visualSummaryModel: "summary-model",
          apiKey: "",
          asrBaseUrl: " https://new-speech.example/v1 ",
          asrModel: " new-asr-model ",
          asrApiKey: "   "
        },
        storedConfig
      )
    ).toEqual({
      baseUrl: "https://new.example/v1",
      providerProfileId: "profile-a",
      model: "new-model",
      viewerModel: "",
      memoryModel: "",
      visualSummaryModel: "summary-model",
      apiKey: "stored-model-key",
      asrBaseUrl: "https://new-speech.example/v1",
      asrModel: "new-asr-model",
      asrApiKey: "stored-asr-key"
    });
  });

  it("migrates missing ASR endpoint fields from legacy renderer payloads", () => {
    const legacyInput = {
      ...storedConfig,
      asrBaseUrl: undefined,
      asrModel: undefined
    } as unknown as ModelConfig;

    expect(resolveModelConfig(legacyInput, storedConfig)).toMatchObject({
      asrBaseUrl: "https://speech.example/v1",
      asrModel: "stored-asr-model"
    });
    expect(resolveModelConfig(legacyInput, null)).toMatchObject({
      asrBaseUrl: "https://api.stepfun.com/v1",
      asrModel: "stepaudio-2.5-asr"
    });
  });

  it("replaces only credentials explicitly entered by the user", () => {
    expect(
      resolveModelConfig(
        {
          baseUrl: "https://new.example/v1",
          providerProfileId: "default",
          model: "new-model",
          viewerModel: "",
          memoryModel: "",
          visualSummaryModel: "",
          apiKey: " new-model-key ",
          asrBaseUrl: "https://new-speech.example/v1",
          asrModel: "new-asr-model",
          asrApiKey: ""
        },
        storedConfig
      )
    ).toMatchObject({
      apiKey: "new-model-key",
      asrApiKey: "stored-asr-key"
    });
  });

  it("requires credentials when none are stored", () => {
    expect(() =>
      resolveModelConfig(
        {
          baseUrl: "https://new.example/v1",
          providerProfileId: "",
          model: "new-model",
          viewerModel: "",
          memoryModel: "",
          visualSummaryModel: "",
          apiKey: "",
          asrBaseUrl: "",
          asrModel: "",
          asrApiKey: ""
        },
        null
      )
    ).toThrow("模型与语音识别的地址、模型名称和密钥均为必填项。");
  });

  it("inherits blank role overrides from the default model", () => {
    expect(resolveModelProvider(storedConfig)).toMatchObject({
      providerProfileId: "stored-profile",
      defaultModel: "stored-model",
      viewerModel: "stored-viewer",
      memoryModel: "stored-model",
      visualSummaryModel: "stored-model"
    });
  });

  it("creates a complete runtime candidate without ASR credentials", () => {
    expect(createRuntimeProviderCandidate(storedConfig)).toEqual({
      provider_profile_id: "stored-profile",
      model_base_url: "https://stored.example/v1",
      model_name: "stored-model",
      viewer_model: "stored-viewer",
      memory_model: "stored-model",
      visual_summary_model: "stored-model",
      model_api_key: "stored-model-key"
    });
  });

  it("revisions an unchanged profile when endpoint or model credentials change", () => {
    const endpointChanged = reviseProviderProfileForActiveSession(
      { ...storedConfig, baseUrl: "https://new.example/v1" },
      storedConfig,
      true,
      "12345678-abcd"
    );
    const keyChanged = reviseProviderProfileForActiveSession(
      { ...storedConfig, apiKey: "new-key" },
      storedConfig,
      true,
      "abcdefgh-1234"
    );

    expect(endpointChanged.providerProfileId).toBe("stored-profile-rev-12345678");
    expect(keyChanged.providerProfileId).toBe("stored-profile-rev-abcdefgh");
    expect(
      reviseProviderProfileForActiveSession(
        { ...storedConfig, providerProfileId: "x".repeat(128), baseUrl: "https://new.example/v1" },
        { ...storedConfig, providerProfileId: "x".repeat(128) },
        true,
        "12345678"
      ).providerProfileId
    ).toHaveLength(128);
    expect(modelProviderChanged(endpointChanged, storedConfig)).toBe(true);
  });

  it("does not revision the profile for ASR-only changes or an explicit profile change", () => {
    expect(
      reviseProviderProfileForActiveSession(
        {
          ...storedConfig,
          asrBaseUrl: "https://new-speech.example/v1",
          asrModel: "new-asr-model",
          asrApiKey: "new-asr-key"
        },
        storedConfig,
        true,
        "12345678"
      ).providerProfileId
    ).toBe("stored-profile");
    expect(
      reviseProviderProfileForActiveSession(
        { ...storedConfig, providerProfileId: "profile-b", baseUrl: "https://new.example/v1" },
        storedConfig,
        true,
        "12345678"
      ).providerProfileId
    ).toBe("profile-b");
    expect(
      modelProviderChanged(
        { ...storedConfig, asrBaseUrl: "https://new-speech.example/v1" },
        storedConfig
      )
    ).toBe(false);
    expect(
      asrProviderChanged(
        { ...storedConfig, asrBaseUrl: "https://new-speech.example/v1" },
        storedConfig
      )
    ).toBe(true);
  });

  it("restarts only to replace a different provider for the next session", async () => {
    const configureProviders = vi
      .fn<(_: ModelConfig) => Promise<void>>()
      .mockRejectedValueOnce({ code: "providers_already_configured" })
      .mockResolvedValueOnce(undefined);
    const restartBackend = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);

    await configureProviderForSession(storedConfig, { configureProviders }, restartBackend);

    expect(restartBackend).toHaveBeenCalledOnce();
    expect(configureProviders).toHaveBeenCalledTimes(2);
    expect(configureProviders).toHaveBeenLastCalledWith(storedConfig);
  });

  it("does not restart for a provider error unrelated to an installed pipeline", async () => {
    expect(isProviderPipelineAlreadyConfigured({ code: "providers_already_configured" })).toBe(true);
    expect(isProviderPipelineAlreadyConfigured({ code: "provider_timeout" })).toBe(false);
    const configureProviders = vi
      .fn<(_: ModelConfig) => Promise<void>>()
      .mockRejectedValueOnce(new Error("provider timeout"));
    const restartBackend = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);

    await expect(
      configureProviderForSession(storedConfig, { configureProviders }, restartBackend)
    ).rejects.toThrow("provider timeout");
    expect(restartBackend).not.toHaveBeenCalled();
  });

});
