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
          "X-ADVX-Protocol-Version": "2"
        })
      })
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).toMatchObject({
      apply_id: "apply-1",
      base_revision: 1,
      audience_contract_version: 1,
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
      protocol_version: 2,
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

  it.each([
    {
      name: "protocol v1",
      message: {
        type: "barrage.event",
        protocol_version: 1,
        barrage: {}
      }
    },
    {
      name: "a protocol v2 barrage without viewer identity",
      message: {
        type: "barrage.event",
        protocol_version: 2,
        barrage: {
          barrage_id: "legacy-barrage",
          room_id: "room-1",
          session_id: "session-1",
          audience_epoch: 3,
          observation_id: "observation-1",
          generation_request_id: "generation-1",
          display_name: "legacy",
          reaction_type: "legacy",
          evidence_refs: [],
          text: "legacy",
          created_at_ms: 100,
          expires_at_ms: Date.now() + 10_000
        }
      }
    }
  ])("rejects $name before notifying barrage listeners", ({ message }) => {
    const client = new BackendClient({ localToken: "token" });
    const listener = vi.fn();
    client.onBarrage(listener);
    const bridge = client as unknown as {
      handleMessage(message: unknown): void;
    };

    bridge.handleMessage(message);

    expect(listener).not.toHaveBeenCalled();
    expect(client.currentStatus()).toMatchObject({
      connection: "failed",
      startupError: expect.stringMatching(/协议|barrage\.(viewer_instance_id|viewer_sequence)/)
    });
  });

  it.each([
    ["fractional audience epoch", { audience_epoch: 1.5 }],
    ["zero viewer sequence", { viewer_sequence: 0 }],
    ["negative creation timestamp", { created_at_ms: -1 }],
    ["fractional creation timestamp", { created_at_ms: 1.5 }],
    ["zero expiry timestamp", { expires_at_ms: 0 }],
    ["expiry before creation", { created_at_ms: 100, expires_at_ms: 100 }],
    ["oversized identifier", { barrage_id: "x".repeat(129) }],
    ["oversized display name", { display_name: "x".repeat(65) }],
    ["oversized reaction type", { reaction_type: "x".repeat(65) }],
    ["oversized text", { text: "x".repeat(201) }],
    [
      "too many evidence references",
      {
        evidence_refs: Array.from({ length: 129 }, () => ({
          source: "event",
          event_id: "event-1",
          frame_index: null
        }))
      }
    ],
    [
      "oversized evidence event id",
      {
        evidence_refs: [{
          source: "event",
          event_id: "x".repeat(129),
          frame_index: null
        }]
      }
    ],
    [
      "event evidence with a frame index",
      { evidence_refs: [{ source: "event", event_id: "event-1", frame_index: 0 }] }
    ],
    [
      "frame evidence with an event id",
      { evidence_refs: [{ source: "frame", event_id: "event-1", frame_index: 0 }] }
    ],
    [
      "fractional frame index",
      { evidence_refs: [{ source: "frame", event_id: null, frame_index: 0.5 }] }
    ],
    [
      "negative frame index",
      { evidence_refs: [{ source: "frame", event_id: null, frame_index: -1 }] }
    ]
  ])("closes malicious barrage %s without notifying listeners", (_, overrides) => {
    const client = new BackendClient({ localToken: "token" });
    const listener = vi.fn();
    const close = vi.fn();
    client.onBarrage(listener);
    const bridge = client as unknown as {
      socket: { close(code: number, reason: string): void };
      parseMessage(value: string): unknown;
      handleMessage(message: unknown): void;
    };
    bridge.socket = { close };

    const parsed = bridge.parseMessage(JSON.stringify({
      type: "barrage.event",
      protocol_version: 2,
      barrage: {
        barrage_id: "barrage-1",
        room_id: "room-1",
        session_id: "session-1",
        audience_epoch: 1,
        observation_id: "observation-1",
        generation_request_id: "generation-1",
        viewer_instance_id: "viewer-1",
        persona_id: "persona-1",
        display_name: "viewer",
        viewer_sequence: 1,
        reaction_type: "comment",
        evidence_refs: [],
        text: "hello",
        created_at_ms: 100,
        expires_at_ms: 200,
        ...overrides
      }
    }));
    if (parsed) bridge.handleMessage(parsed);

    expect(listener).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledWith(1002, "invalid protocol message");
  });

  it.each([
    [
      "backend.ready with an unknown session state",
      {
        type: "backend.ready",
        protocol_version: 2,
        session: {
          session_id: null,
          state: "connected",
          started_at_ms: null,
          updated_at_ms: 0,
          revision: 0
        }
      }
    ],
    [
      "session.status with a fractional timestamp",
      {
        type: "session.status",
        protocol_version: 2,
        session: {
          session_id: "session-1",
          state: "running",
          started_at_ms: 1,
          updated_at_ms: 1.5,
          revision: 1
        }
      }
    ],
    [
      "session.status with a negative revision",
      {
        type: "session.status",
        protocol_version: 2,
        session: {
          session_id: "session-1",
          state: "running",
          started_at_ms: 1,
          updated_at_ms: 1,
          revision: -1
        }
      }
    ],
    [
      "backend.pong with a non-string request ID",
      { type: "backend.pong", protocol_version: 2, request_id: 42 }
    ],
    [
      "ingest.ack with an oversized identifier",
      {
        type: "ingest.ack",
        protocol_version: 2,
        session_id: "x".repeat(129),
        input_id: "input-1",
        input_kind: "text",
        stage: "received",
        accepted_at_ms: 1
      }
    ],
    [
      "ingest.ack with an unknown input kind",
      {
        type: "ingest.ack",
        protocol_version: 2,
        session_id: "session-1",
        input_id: "input-1",
        input_kind: "video",
        stage: "received",
        accepted_at_ms: 1
      }
    ],
    [
      "ingest.ack with an unknown stage",
      {
        type: "ingest.ack",
        protocol_version: 2,
        session_id: "session-1",
        input_id: "input-1",
        input_kind: "text",
        stage: "published",
        accepted_at_ms: 1
      }
    ],
    [
      "ingest.ack with a fractional timestamp",
      {
        type: "ingest.ack",
        protocol_version: 2,
        session_id: "session-1",
        input_id: "input-1",
        input_kind: "text",
        stage: "received",
        accepted_at_ms: 1.5
      }
    ],
    [
      "ingest.ack with a negative timestamp",
      {
        type: "ingest.ack",
        protocol_version: 2,
        session_id: "session-1",
        input_id: "input-1",
        input_kind: "text",
        stage: "received",
        accepted_at_ms: -1
      }
    ],
    [
      "ingest.ack with an extra field",
      {
        type: "ingest.ack",
        protocol_version: 2,
        session_id: "session-1",
        input_id: "input-1",
        input_kind: "text",
        stage: "received",
        accepted_at_ms: 1,
        injected: true
      }
    ],
    [
      "ingest.rejected with an unknown rejection code",
      {
        type: "ingest.rejected",
        protocol_version: 2,
        code: "not_really_rejected",
        message: "no"
      }
    ],
    [
      "ingest.rejected with an oversized message",
      {
        type: "ingest.rejected",
        protocol_version: 2,
        code: "invalid_input",
        message: "x".repeat(257)
      }
    ],
    [
      "ingest.rejected with an empty optional input ID",
      {
        type: "ingest.rejected",
        protocol_version: 2,
        code: "invalid_input",
        message: "no",
        input_id: ""
      }
    ],
    [
      "ingest.rejected with an invalid optional input kind",
      {
        type: "ingest.rejected",
        protocol_version: 2,
        code: "invalid_input",
        message: "no",
        input_kind: "camera"
      }
    ],
    [
      "protocol.error with an unknown protocol code",
      {
        type: "protocol.error",
        protocol_version: 2,
        code: "invalid_version",
        message: "no"
      }
    ],
    [
      "protocol.error with an empty message",
      {
        type: "protocol.error",
        protocol_version: 2,
        code: "invalid_message",
        message: ""
      }
    ],
    [
      "protocol.error with a fractional supported version",
      {
        type: "protocol.error",
        protocol_version: 2,
        code: "version_mismatch",
        message: "no",
        supported_version: 1.5
      }
    ]
  ])("rejects malicious non-barrage envelope: %s", (_, message) => {
    const client = new BackendClient({ localToken: "token" });
    const barrageListener = vi.fn();
    const close = vi.fn();
    client.onBarrage(barrageListener);
    const bridge = client as unknown as {
      socket: { close(code: number, reason: string): void };
      parseMessage(value: string): unknown;
    };
    bridge.socket = { close };

    expect(bridge.parseMessage(JSON.stringify(message))).toBeNull();

    expect(barrageListener).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledWith(1002, "invalid protocol message");
    expect(client.currentStatus()).toMatchObject({
      connection: "failed",
      startupError: expect.any(String),
      session: { sessionId: null, state: "idle", revision: 0 }
    });
  });

  it.each([
    [
      "protocol.error without supported_version",
      {
        type: "protocol.error",
        protocol_version: 2,
        code: "invalid_message",
        message: "invalid"
      }
    ],
    [
      "ingest.rejected without optional identity fields",
      {
        type: "ingest.rejected",
        protocol_version: 2,
        code: "invalid_input",
        message: "invalid"
      }
    ],
    [
      "backend.pong with an empty unconstrained request ID",
      { type: "backend.pong", protocol_version: 2, request_id: "" }
    ]
  ])("accepts generated/Pydantic optional boundary: %s", (_, message) => {
    const client = new BackendClient({ localToken: "token" });
    const close = vi.fn();
    const bridge = client as unknown as {
      socket: { close(code: number, reason: string): void };
      parseMessage(value: string): unknown;
    };
    bridge.socket = { close };

    expect(bridge.parseMessage(JSON.stringify(message))).not.toBeNull();
    expect(close).not.toHaveBeenCalled();
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
      startupError: expect.stringContaining("需要 v2")
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
