import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { createInitialAudienceWorkspace } from "../../shared/audience";
import { compileCanonicalRuntimeSpec } from "../../shared/backend-client";
import type {
  BackendBarrageEvent,
  RuntimeModelProviderCandidate
} from "../../shared/contracts";
import { BackendClient } from "./backend-client";

const providerCandidate: RuntimeModelProviderCandidate = {
  provider_profile_id: "default",
  model_base_url: "https://api.example.com/v1",
  model_name: "viewer-model",
  director_model: "viewer-model",
  viewer_model: "viewer-model",
  memory_model: "viewer-model",
  visual_summary_model: "viewer-model",
  model_api_key: "model-key"
};

describe("BackendClient startup state", () => {
  it("reports startup before the process is ready", async () => {
    const client = new BackendClient({ localToken: "token" });
    expect(await client.status()).toMatchObject({
      connection: "starting",
      startupError: null
    });
  });

  it("reports a user-facing startup failure and can return to startup", () => {
    const client = new BackendClient({ localToken: "token" });
    client.failStartup(new Error("后端文件缺失"));
    expect(client.currentStatus()).toMatchObject({
      connection: "failed",
      startupError: "后端文件缺失"
    });

    client.beginStartup();
    expect(client.currentStatus()).toMatchObject({
      connection: "starting",
      startupError: null
    });
  });
});

describe("BackendClient runtime v2", () => {
  it("queries AI calls with the complete debug filter set", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_cursor: null, metadata: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    const client = new BackendClient({
      baseUrl: "http://127.0.0.1:9999",
      localToken: "token"
    });

    await client.queryAiCalls({
      sessionId: "session/a",
      role: "viewer",
      status: "failed",
      correlationId: "corr/a",
      cursor: "cursor/a",
      limit: 250
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:9999/debug/ai-calls?" +
      "session_id=session%2Fa&role=viewer&status=failed&" +
      "correlation_id=corr%2Fa&cursor=cursor%2Fa&limit=250"
    );
    fetchMock.mockRestore();
  });

  it("treats an already-gone backend session as an idempotent stop", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          detail: {
            code: "session_not_found",
            message: "session stale-session is not active"
          }
        }), {
          status: 404,
          headers: { "Content-Type": "application/json" }
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          session_id: null,
          state: "idle",
          started_at_ms: null,
          updated_at_ms: 20,
          revision: 3
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        })
      );
    const client = new BackendClient({
      baseUrl: "http://127.0.0.1:9999",
      localToken: "token"
    });
    const bridge = client as unknown as {
      session: {
        sessionId: string;
        state: "running";
        startedAtMs: number;
        updatedAtMs: number;
        revision: number;
      };
    };
    bridge.session = {
      sessionId: "stale-session",
      state: "running",
      startedAtMs: 1,
      updatedAtMs: 2,
      revision: 2
    };

    await expect(client.stopSession()).resolves.toMatchObject({
      sessionId: null,
      state: "idle"
    });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:9999/sessions/stale-session/stop",
      "http://127.0.0.1:9999/sessions/current"
    ]);
    expect(client.currentStatus().session).toMatchObject({
      sessionId: null,
      state: "idle"
    });
    fetchMock.mockRestore();
  });

  it("queries and moderates session-scoped Viewer identities", async () => {
    const viewer = {
      viewer_instance_id: "viewer/a",
      username: "pixel-user",
      display_name: "pixel-user",
      avatar_seed: "avatar-1",
      color_seed: "color-1",
      persona_id: "curious",
      persona_display_name: "Curious",
      presence_state: "active",
      joined_at_ms: 1,
      last_left_at_ms: null,
      join_count: 1,
      muted_until_ms: null,
      viewer_sequence: 0,
      presence_revision: 1,
      moderation_revision: 1
    };
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session_id: "session/a",
        room_id: "room-1",
        audience_epoch: 1,
        population_revision: 1,
        target_concurrent_viewers: 1,
        active_count: 1,
        viewers: [viewer]
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockImplementation(async () => new Response(JSON.stringify(viewer), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      }));
    const client = new BackendClient({
      baseUrl: "http://127.0.0.1:9999",
      localToken: "token"
    });

    await client.queryAudience("session/a");
    await client.muteViewer("session/a", "viewer/a", 60_000, "host moderation");
    await client.unmuteViewer("session/a", "viewer/a");
    await client.kickViewer("session/a", "viewer/a", "host moderation");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:9999/runtime/sessions/session%2Fa/audience",
      "http://127.0.0.1:9999/runtime/sessions/session%2Fa/viewers/viewer%2Fa/mute",
      "http://127.0.0.1:9999/runtime/sessions/session%2Fa/viewers/viewer%2Fa/unmute",
      "http://127.0.0.1:9999/runtime/sessions/session%2Fa/viewers/viewer%2Fa/kick"
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
      command_id: expect.any(String),
      duration_ms: 60_000,
      reason: "host moderation"
    });
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      command_id: expect.any(String)
    });
    expect(JSON.parse(fetchMock.mock.calls[3][1]?.body as string)).toEqual({
      command_id: expect.any(String),
      reason: "host moderation"
    });
    fetchMock.mockRestore();
  });

  it("sends structured Viewer and Persona targets without embedding them in text", async () => {
    const client = new BackendClient({ localToken: "token" });
    const sendJson = vi.fn();
    const bridge = client as unknown as {
      session: {
        sessionId: string;
        state: "running";
        startedAtMs: number;
        updatedAtMs: number;
        revision: number;
      };
      waitForIngest(): Promise<void>;
      sendJson(message: unknown): void;
    };
    bridge.session = {
      sessionId: "session-1",
      state: "running",
      startedAtMs: 1,
      updatedAtMs: 1,
      revision: 1
    };
    bridge.waitForIngest = async () => undefined;
    bridge.sendJson = sendJson;

    await client.submitText("text-1", 10, "你好", {
      targetViewerId: "viewer:room:critic:01"
    });

    expect(sendJson).toHaveBeenCalledWith(expect.objectContaining({
      text: "你好",
      target_viewer_id: "viewer:room:critic:01",
      target_persona_id: undefined
    }));
  });

  it("uses shared-brain authoritative memory and meme endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({
      baseUrl: "http://127.0.0.1:9999",
      localToken: "token"
    });

    await client.listRoomMemories("room/a");
    await client.getRoomMemoryHead("room/a");
    await client.listModeMemes("mode/a");
    await client.mutateModeMeme("mode/a", "meme/a", "disable", 3);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:9999/shared-brain/rooms/room%2Fa/memories",
      "http://127.0.0.1:9999/shared-brain/rooms/room%2Fa/memory-head",
      "http://127.0.0.1:9999/shared-brain/modes/mode%2Fa/memes",
      "http://127.0.0.1:9999/shared-brain/modes/mode%2Fa/memes/meme%2Fa/disable"
    ]);
    expect(JSON.parse(fetchMock.mock.calls[3][1]?.body as string))
      .toEqual({ expected_revision: 3 });
    fetchMock.mockRestore();
  });

  it("imports legacy memes through the dedicated provenance endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        candidate_id: "candidate-1",
        meme_id: "meme-1",
        provenance_event_id: "event-1",
        created: true
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({
      baseUrl: "http://127.0.0.1:9999",
      localToken: "token"
    });

    await client.importLegacyMeme("mode/a", {
      room_id: "room-a",
      session_id: "session-a",
      audience_epoch: 2,
      legacy_meme_id: "old-joke",
      text: "这波属于是",
      legacy_created_at_ms: 123
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:9999/shared-brain/modes/mode%2Fa/legacy-memes/import"
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      room_id: "room-a",
      session_id: "session-a",
      audience_epoch: 2,
      legacy_meme_id: "old-joke",
      text: "这波属于是",
      legacy_created_at_ms: 123
    });
    fetchMock.mockRestore();
  });

  it("configures one provider profile with optional role overrides and ASR", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });

    await client.configureProviders({
      baseUrl: "https://api.example.com/v1",
      providerProfileId: "profile-a",
      model: "default-model",
      directorModel: "director-model",
      viewerModel: "",
      memoryModel: "memory-model",
      visualSummaryModel: "",
      apiKey: "model-key",
      asrApiKey: "asr-key"
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).toEqual({
      provider_profile_id: "profile-a",
      model_base_url: "https://api.example.com/v1",
      model_name: "default-model",
      director_model: "director-model",
      memory_model: "memory-model",
      model_api_key: "model-key",
      asr_api_key: "asr-key"
    });
    fetchMock.mockRestore();
  });

  it("uses a probe timeout that covers every bounded upstream phase", async () => {
    const timeoutSpy = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(new AbortController().signal);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });

    try {
      await client.probeProvider();
      expect(timeoutSpy).toHaveBeenCalledWith(130_000);
    } finally {
      fetchMock.mockRestore();
      timeoutSpy.mockRestore();
    }
  });

  it("reports provider probe timeouts without claiming the backend is unavailable", async () => {
    const timeoutError = new Error("The operation was aborted due to timeout");
    timeoutError.name = "TimeoutError";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(timeoutError);
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });

    try {
      await expect(client.probeProvider()).rejects.toMatchObject({
        name: "BackendClientError",
        code: "provider_probe_timeout",
        message: "Provider 能力探测超时，请检查上游服务连接。"
      });
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("gives runtime startup enough time for the model and ASR capability checks", async () => {
    const timeoutSpy = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(new AbortController().signal);
    const compiled = compileCanonicalRuntimeSpec(createInitialAudienceWorkspace(), {
      configRevision: 1,
      provider: {
        providerProfileId: "default",
        directorModel: "viewer-model",
        viewerModel: "viewer-model",
        memoryModel: "viewer-model",
        visualSummaryModel: "viewer-model"
      }
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ configured: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ).mockResolvedValueOnce(
      new Response(JSON.stringify({
        room_id: "default-room",
        session_id: "session-1",
        audience_epoch: 1,
        config_revision: 1,
        config_hash: compiled.configHash,
        canonical_runtime_spec: compiled.spec,
        viewers: [],
        recovered: false
      }), { status: 201, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });
    const bridge = client as unknown as { ensureConnected(): Promise<void> };
    bridge.ensureConnected = async () => undefined;

    try {
      await client.startSession("request-1", compiled);
      expect(timeoutSpy).toHaveBeenNthCalledWith(1, 8_000);
      expect(timeoutSpy).toHaveBeenNthCalledWith(2, 180_000);
    } finally {
      fetchMock.mockRestore();
      timeoutSpy.mockRestore();
    }
  });

  it("reports a runtime startup timeout as a provider or ASR issue", async () => {
    const timeoutError = new Error("The operation was aborted due to timeout");
    timeoutError.name = "TimeoutError";
    const compiled = compileCanonicalRuntimeSpec(createInitialAudienceWorkspace(), {
      configRevision: 1,
      provider: {
        providerProfileId: "default",
        directorModel: "viewer-model",
        viewerModel: "viewer-model",
        memoryModel: "viewer-model",
        visualSummaryModel: "viewer-model"
      }
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ configured: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ).mockRejectedValueOnce(timeoutError);
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });
    const bridge = client as unknown as { ensureConnected(): Promise<void> };
    bridge.ensureConnected = async () => undefined;

    try {
      await expect(client.startSession("request-1", compiled)).rejects.toMatchObject({
        name: "BackendClientError",
        code: "runtime_session_start_timeout",
        message: "AI 观众初始化超时，请检查 Provider 和 ASR 服务连接。"
      });
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("keeps the last runtime session as an explicit recovery candidate after restart", () => {
    const client = new BackendClient({ localToken: "token" });
    const bridge = client as unknown as {
      session: {
        sessionId: string;
        state: "running";
        startedAtMs: number;
        updatedAtMs: number;
        revision: number;
      };
    };
    bridge.session = {
      sessionId: "session-previous",
      state: "running",
      startedAtMs: 1,
      updatedAtMs: 1,
      revision: 2
    };

    client.beginStartup();

    expect(client.currentStatus()).toMatchObject({
      recoverableRuntimeSessionId: "session-previous",
      session: { sessionId: null, state: "idle" }
    });
  });

  it("recovers explicitly without silently replaying through startup", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        room_id: "default-room",
        session_id: "session-previous",
        audience_epoch: 4,
        config_revision: 2,
        config_hash: "b".repeat(64),
        canonical_runtime_spec: {},
        viewers: [],
        recovered: true
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({
      baseUrl: "http://127.0.0.1:9999",
      localToken: "token"
    });
    client.restoreRecoverableRuntimeSession("session-previous");

    const recovered = await client.recoverRuntime("session-previous");

    expect(recovered).toMatchObject({ audience_epoch: 4, recovered: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:9999/runtime/sessions/session-previous/recover",
      expect.objectContaining({ method: "POST" })
    );
    expect(client.currentStatus()).toMatchObject({
      recoverableRuntimeSessionId: null,
      session: { sessionId: "session-previous", state: "running", revision: 2 }
    });
    fetchMock.mockRestore();
  });

  it("submits session-scoped atomic apply requests with protocol v2", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        room_id: "default-room",
        session_id: "session-1",
        audience_epoch: 2,
        config_revision: 2,
        config_hash: "a".repeat(64),
        canonical_runtime_spec: {},
        viewers: [],
        recovered: false
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });
    const compiled = compileCanonicalRuntimeSpec(createInitialAudienceWorkspace(), {
      configRevision: 2,
      provider: {
        providerProfileId: "default",
        directorModel: "viewer-model",
        viewerModel: "viewer-model",
        memoryModel: "viewer-model",
        visualSummaryModel: "viewer-model"
      }
    });

    await client.applyRuntime("session-1", "apply-1", 1, compiled, providerCandidate);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:9999/runtime/sessions/session-1/apply",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-ADVX-Protocol-Version": "3"
        })
      })
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).toMatchObject({
      apply_id: "apply-1",
      base_revision: 1,
      audience_contract_version: 2,
      client_config_hash: compiled.configHash,
      provider_candidate: providerCandidate
    });
    expect(body.provider_candidate).not.toHaveProperty("asr_api_key");
    fetchMock.mockRestore();
  });

  it("omits the provider candidate when an apply keeps the active provider spec", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        room_id: "default-room",
        session_id: "session-1",
        audience_epoch: 2,
        config_revision: 2,
        config_hash: "a".repeat(64),
        canonical_runtime_spec: {},
        viewers: [],
        recovered: false
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });
    const compiled = compileCanonicalRuntimeSpec(createInitialAudienceWorkspace(), {
      configRevision: 2,
      provider: {
        providerProfileId: "default",
        directorModel: "viewer-model",
        viewerModel: "viewer-model",
        memoryModel: "viewer-model",
        visualSummaryModel: "viewer-model"
      }
    });
    (client as unknown as { runtime: { canonical_runtime_spec: typeof compiled.spec } }).runtime = {
      canonical_runtime_spec: compiled.spec
    };

    await client.applyRuntime("session-1", "apply-1", 1, compiled, providerCandidate);

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).not.toHaveProperty("provider_candidate");
    fetchMock.mockRestore();
  });

  it("omits the provider candidate when rollback keeps the active provider", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        room_id: "default-room",
        session_id: "session-1",
        audience_epoch: 3,
        config_revision: 1,
        config_hash: "a".repeat(64),
        canonical_runtime_spec: { provider: runtimeProvider },
        viewers: [],
        recovered: false
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });
    const bridge = client as unknown as {
      runtime: {
        session_id: string;
        canonical_runtime_spec: { provider: typeof runtimeProvider };
      };
      runtimeProvidersByRevision: Map<string, typeof runtimeProvider>;
    };
    bridge.runtime = {
      session_id: "session-1",
      canonical_runtime_spec: { provider: runtimeProvider }
    };
    bridge.runtimeProvidersByRevision.set("session-1:1", runtimeProvider);

    await client.rollbackRuntime("session-1", "rollback-1", 2, 1, providerCandidate);

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).not.toHaveProperty("provider_candidate");
    fetchMock.mockRestore();
  });

  it("carries a matching provider candidate when rollback changes providers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        room_id: "default-room",
        session_id: "session-1",
        audience_epoch: 3,
        config_revision: 1,
        config_hash: "a".repeat(64),
        canonical_runtime_spec: { provider: runtimeProvider },
        viewers: [],
        recovered: false
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    );
    const client = new BackendClient({ baseUrl: "http://127.0.0.1:9999", localToken: "token" });
    const changedProvider = { ...runtimeProvider, viewer_model: "new-viewer-model" };
    const bridge = client as unknown as {
      runtime: {
        session_id: string;
        canonical_runtime_spec: { provider: typeof runtimeProvider };
      };
      runtimeProvidersByRevision: Map<string, typeof runtimeProvider>;
    };
    bridge.runtime = {
      session_id: "session-1",
      canonical_runtime_spec: { provider: changedProvider }
    };
    bridge.runtimeProvidersByRevision.set("session-1:1", runtimeProvider);

    await client.rollbackRuntime("session-1", "rollback-1", 2, 1, providerCandidate);

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body.provider_candidate).toEqual(providerCandidate);
    expect(body.provider_candidate).not.toHaveProperty("asr_api_key");
    fetchMock.mockRestore();
  });

  it("maps viewer-aware realtime barrages without losing trace identity", () => {
    const client = new BackendClient({ localToken: "token" });
    let received: BackendBarrageEvent | null = null;
    client.onBarrage((event) => {
      received = event;
    });
    const bridge = client as unknown as {
      session: { sessionId: string };
      runtime: { audience_epoch: number };
      handleMessage(message: object): void;
    };
    bridge.session = { sessionId: "session-1" };
    bridge.runtime = { audience_epoch: 3 };
    bridge.handleMessage({
      type: "barrage.event",
      protocol_version: 3,
      barrage: {
        barrage_id: "barrage-1",
        room_id: "room-1",
        session_id: "session-1",
        audience_epoch: 3,
        observation_id: "observation-1",
        generation_request_id: "generation-1",
        viewer_instance_id: "viewer-1",
        persona_id: "reaction_qmark",
        display_name: "问号哥·01",
        viewer_sequence: 2,
        reaction_type: "surprise",
        intent: "react_to_host",
        target: { kind: "host", viewer_instance_id: null, event_id: null },
        evidence_refs: [{ source: "event", event_id: "event-1", frame_index: null }],
        text: "？？",
        created_at_ms: 100,
        expires_at_ms: Date.now() + 10_000
      }
    });
    expect(received).toMatchObject({
      barrageId: "barrage-1",
      audienceId: "viewer-1",
      audienceName: "问号哥·01",
      audienceEpoch: 3,
      observationId: "observation-1",
      generationRequestId: "generation-1",
      personaId: "reaction_qmark",
      viewerSequence: 2
    });
  });

  it("closes one representative malformed realtime envelope", () => {
    const client = new BackendClient({ localToken: "token" });
    const close = vi.fn();
    const bridge = client as unknown as {
      socket: { close(code: number, reason: string): void };
      parseMessage(value: string): unknown;
    };
    bridge.socket = { close };

    expect(bridge.parseMessage(JSON.stringify({
      type: "backend.pong",
      protocol_version: 3,
      request_id: 42
    }))).toBeNull();
    expect(close).toHaveBeenCalledWith(1002, "invalid protocol message");
  });

  it("rejects a v3 barrage without Viewer identity", () => {
    const client = new BackendClient({ localToken: "token" });
    const listener = vi.fn();
    const close = vi.fn();
    client.onBarrage(listener);
    const bridge = client as unknown as {
      socket: { close(code: number, reason: string): void };
      parseMessage(value: string): unknown;
    };
    bridge.socket = { close };

    expect(bridge.parseMessage(JSON.stringify({
      type: "barrage.event",
      protocol_version: 3,
      barrage: {
        barrage_id: "barrage-1",
        room_id: "room-1",
        session_id: "session-1",
        audience_epoch: 1,
        observation_id: "observation-1",
        generation_request_id: "generation-1",
        persona_id: "persona-1",
        display_name: "viewer",
        viewer_sequence: 1,
        reaction_type: "comment",
        evidence_refs: [],
        text: "hello",
        created_at_ms: 100,
        expires_at_ms: 200
      }
    }))).toBeNull();
    expect(listener).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledWith(1002, "invalid protocol message");
  });

  it("rejects protocol v1 during WebSocket parsing instead of asserting it as v2", () => {
    const client = new BackendClient({ localToken: "token" });
    const bridge = client as unknown as {
      parseMessage(value: string): unknown;
    };

    expect(bridge.parseMessage(JSON.stringify({
      type: "barrage.event",
      protocol_version: 1,
      barrage: {}
    }))).toBeNull();
    expect(client.currentStatus()).toMatchObject({
      connection: "failed",
      startupError: expect.stringContaining("需要 v3")
    });
  });
});

const runtimeProvider = {
  provider_profile_id: "default",
  director_model: "viewer-model",
  viewer_model: "viewer-model",
  memory_model: "viewer-model",
  visual_summary_model: "viewer-model"
};
