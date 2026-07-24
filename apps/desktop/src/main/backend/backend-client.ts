import type {
  RealtimeIngestAck,
  RealtimeIngestRejected,
  RealtimeServerMessage,
  SessionSnapshot
} from "@advx/contracts";
import type {
  BackendBarrageEvent,
  BackendConnectionState,
  BackendRuntimeStatus,
  BackendSessionSnapshot,
  ModelConfig,
  RuntimeModelProviderCandidate
} from "../../shared/contracts";
import type {
  AutoIngestResponse,
  CandidateCommitResponse,
  CompiledRuntimeSpec,
  DebugTraceQueryResult,
  LegacyMemeImportRequest,
  LegacyMemeImportResponse,
  MemeCandidate,
  MemoryResetResponse,
  ModeMeme,
  ModeMemeEdit,
  ProviderProbeResult,
  RoomLongTermMemory,
  RoomMemoryEdit,
  RoomMemoryHead,
  RuntimeApplySnapshot,
  RuntimeQuerySnapshot,
  TextSubmitTarget
} from "../../shared/backend-client";
import { encodeBinaryEnvelope } from "./realtime-binary";

const PROTOCOL_VERSION = 2;
const PROTOCOL_HEADER = "X-ADVX-Protocol-Version";
const INGEST_ACK_TIMEOUT_MS = 10_000;
const CONNECT_TIMEOUT_MS = 8_000;
// The probe has four sequential upstream phases, each bounded at 30 seconds.
const PROVIDER_PROBE_TIMEOUT_MS = 130_000;
// Starting a runtime repeats the model probe and adds a final-ASR check (35 seconds).
const RUNTIME_SESSION_START_TIMEOUT_MS = 180_000;

type RequestOptions = {
  timeoutMs?: number;
  timeoutCode?: string;
  timeoutMessage?: string;
};

type PendingIngest = {
  resolve: () => void;
  reject: (error: Error) => void;
  timeout: NodeJS.Timeout;
};

type StatusListener = (status: BackendRuntimeStatus) => void;
type BarrageListener = (event: BackendBarrageEvent) => void;

export class BackendClientError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "BackendClientError";
    this.code = code;
  }
}

export class BackendClient {
  private readonly baseUrl: string;
  private readonly websocketUrl: string;
  private readonly localToken: string;
  private socket: WebSocket | null = null;
  private connectPromise: Promise<void> | null = null;
  private connection: BackendConnectionState = "starting";
  private providersConfigured = false;
  private startupError: string | null = null;
  private session: BackendSessionSnapshot = idleSession();
  private runtime: RuntimeQuerySnapshot | null = null;
  private readonly runtimeProvidersByRevision = new Map<string, CanonicalRuntimeSpecProvider>();
  private recoverableRuntimeSessionId: string | null = null;
  private stopped = true;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private readonly pendingIngest = new Map<string, PendingIngest>();
  private readonly statusListeners = new Set<StatusListener>();
  private readonly barrageListeners = new Set<BarrageListener>();
  private audioQueue: Promise<void> = Promise.resolve();

  constructor(options: { baseUrl?: string; localToken: string }) {
    this.baseUrl = (options.baseUrl ?? "http://127.0.0.1:8765").replace(/\/$/, "");
    this.websocketUrl = this.baseUrl.replace(/^http/, "ws") + "/ws";
    this.localToken = options.localToken;
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.snapshot());
    return () => this.statusListeners.delete(listener);
  }

  onBarrage(listener: BarrageListener): () => void {
    this.barrageListeners.add(listener);
    return () => this.barrageListeners.delete(listener);
  }

  async start(): Promise<void> {
    this.stopped = false;
    this.startupError = null;
    await this.ensureConnected();
    await this.refreshConfigurationStatus();
  }

  beginStartup(): void {
    this.stopped = true;
    this.startupError = null;
    if (
      this.session.sessionId &&
      (this.session.state === "running" || this.session.state === "paused")
    ) {
      this.recoverableRuntimeSessionId = this.session.sessionId;
      this.session = idleSession();
    }
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.setConnection("starting");
  }

  failStartup(error: unknown): void {
    this.stopped = true;
    this.startupError =
      error instanceof Error && error.message ? error.message : "本地后端启动失败。";
    if (this.connection === "failed") this.emitStatus();
    else this.setConnection("failed");
  }

  currentStatus(): BackendRuntimeStatus {
    return this.snapshot();
  }

  restoreRecoverableRuntimeSession(sessionId: string): void {
    const normalized = sessionId.trim();
    if (!normalized) return;
    this.recoverableRuntimeSessionId = normalized;
    this.emitStatus();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.rejectPending(new BackendClientError("connection_closed", "Backend connection closed."));
    const socket = this.socket;
    this.socket = null;
    socket?.close(1000, "desktop shutdown");
    this.setConnection("disconnected");
  }

  async status(): Promise<BackendRuntimeStatus> {
    if (this.stopped) return this.snapshot();
    await this.ensureConnected();
    await Promise.all([this.refreshConfigurationStatus(), this.refreshSessionStatus()]);
    return this.snapshot();
  }

  async configureProviders(config: ModelConfig): Promise<void> {
    await this.request("/configuration/providers", "PUT", {
      model_base_url: config.baseUrl,
      provider_profile_id: config.providerProfileId,
      model_name: config.model,
      director_model: config.directorModel || undefined,
      viewer_model: config.viewerModel || undefined,
      memory_model: config.memoryModel || undefined,
      visual_summary_model: config.visualSummaryModel || undefined,
      model_api_key: config.apiKey,
      asr_api_key: config.asrApiKey
    });
    this.providersConfigured = true;
    this.emitStatus();
  }

  async startSession(
    clientRequestId: string,
    runtime: CompiledRuntimeSpec
  ): Promise<BackendSessionSnapshot> {
    await this.ensureConnected();
    await this.refreshConfigurationStatus();
    if (!this.providersConfigured) {
      throw new BackendClientError(
        "providers_not_configured",
        "请先在设置中保存模型和语音识别配置。"
      );
    }
    const started = await this.request<RuntimeQuerySnapshot>(
      "/runtime/sessions",
      "POST",
      {
        client_request_id: clientRequestId,
        canonical_runtime_spec: runtime.spec,
        client_config_hash: runtime.configHash
      },
      {
        timeoutMs: RUNTIME_SESSION_START_TIMEOUT_MS,
        timeoutCode: "runtime_session_start_timeout",
        timeoutMessage: "AI 观众初始化超时，请检查 Provider 和 ASR 服务连接。"
      }
    );
    this.runtimeProvidersByRevision.clear();
    this.rememberRuntime(started);
    this.recoverableRuntimeSessionId = null;
    const now = Date.now();
    this.session = {
      sessionId: started.session_id,
      state: "running",
      startedAtMs: now,
      updatedAtMs: now,
      revision: started.config_revision
    };
    this.emitStatus();
    return this.session;
  }

  async queryRuntime(sessionId: string): Promise<RuntimeQuerySnapshot> {
    const runtime = await this.request<RuntimeQuerySnapshot>(
      `/runtime/sessions/${encodeURIComponent(sessionId)}`,
      "GET"
    );
    this.rememberRuntime(runtime);
    return runtime;
  }

  runtimeProviderAtRevision(
    sessionId: string,
    revision: number
  ): RuntimeQuerySnapshot["canonical_runtime_spec"]["provider"] | null {
    return this.runtimeProvidersByRevision.get(
      runtimeProviderRevisionKey(sessionId, revision)
    ) ?? null;
  }

  async applyRuntime(
    sessionId: string,
    applyId: string,
    baseRevision: number,
    compiled: CompiledRuntimeSpec,
    providerCandidate: RuntimeModelProviderCandidate
  ): Promise<RuntimeApplySnapshot> {
    const providerChanged =
      this.runtime === null ||
      !sameRuntimeProvider(
        this.runtime.canonical_runtime_spec.provider,
        compiled.spec.provider
      );
    const runtime = await this.request<RuntimeApplySnapshot>(
      `/runtime/sessions/${encodeURIComponent(sessionId)}/apply`,
      "POST",
      {
        apply_id: applyId,
        base_revision: baseRevision,
        audience_contract_version: 1,
        canonical_runtime_spec: compiled.spec,
        client_config_hash: compiled.configHash,
        provider_candidate: providerChanged ? providerCandidate : undefined
      }
    );
    this.rememberRuntime(runtime);
    return runtime;
  }

  async rollbackRuntime(
    sessionId: string,
    applyId: string,
    baseRevision: number,
    targetRevision: number,
    providerCandidate: RuntimeModelProviderCandidate
  ): Promise<RuntimeApplySnapshot> {
    const currentProvider =
      this.runtime?.session_id === sessionId
        ? this.runtime.canonical_runtime_spec.provider
        : undefined;
    const targetProvider = this.runtimeProvidersByRevision.get(
      runtimeProviderRevisionKey(sessionId, targetRevision)
    );
    const providerChanged =
      currentProvider !== undefined &&
      targetProvider !== undefined &&
      !sameRuntimeProvider(currentProvider, targetProvider);
    const providerCandidateMatchesTarget =
      targetProvider !== undefined &&
      sameRuntimeProviderCandidate(targetProvider, providerCandidate);
    const runtime = await this.request<RuntimeApplySnapshot>(
      `/runtime/sessions/${encodeURIComponent(sessionId)}/rollback`,
      "POST",
      {
        apply_id: applyId,
        base_revision: baseRevision,
        target_revision: targetRevision,
        audience_contract_version: 1,
        provider_candidate:
          providerChanged && providerCandidateMatchesTarget ? providerCandidate : undefined
      }
    );
    this.rememberRuntime(runtime);
    return runtime;
  }

  async recoverRuntime(sessionId: string): Promise<RuntimeQuerySnapshot> {
    const runtime = await this.request<RuntimeQuerySnapshot>(
      `/runtime/sessions/${encodeURIComponent(sessionId)}/recover`,
      "POST"
    );
    this.rememberRuntime(runtime);
    this.recoverableRuntimeSessionId = null;
    const now = Date.now();
    this.session = {
      sessionId: runtime.session_id,
      state: "running",
      startedAtMs: now,
      updatedAtMs: now,
      revision: runtime.config_revision
    };
    this.emitStatus();
    return runtime;
  }

  async probeProvider(): Promise<ProviderProbeResult> {
    return this.request("/configuration/providers/probe", "POST", undefined, {
      timeoutMs: PROVIDER_PROBE_TIMEOUT_MS,
      timeoutCode: "provider_probe_timeout",
      timeoutMessage: "Provider 能力探测超时，请检查上游服务连接。"
    });
  }

  async queryDebugTraces(
    sessionId: string,
    cursor?: string
  ): Promise<DebugTraceQueryResult> {
    const query = new URLSearchParams({ session_id: sessionId });
    if (cursor) query.set("cursor", cursor);
    return this.request(`/debug/traces?${query.toString()}`, "GET");
  }

  async pauseSession(): Promise<BackendSessionSnapshot> {
    return this.sessionCommand("pause");
  }

  async resumeSession(): Promise<BackendSessionSnapshot> {
    return this.sessionCommand("resume");
  }

  async stopSession(): Promise<BackendSessionSnapshot> {
    const session = await this.sessionCommand("stop");
    this.runtime = null;
    this.runtimeProvidersByRevision.clear();
    this.recoverableRuntimeSessionId = null;
    return session;
  }

  async submitText(
    inputId: string,
    createdAtMs: number,
    text: string,
    target: TextSubmitTarget = {}
  ): Promise<void> {
    const sessionId = this.requireRunningSession();
    const acknowledgement = this.waitForIngest(inputId, "received");
    this.sendJson({
      type: "client.text.submit",
      protocol_version: PROTOCOL_VERSION,
      session_id: sessionId,
      input_id: inputId,
      created_at_ms: createdAtMs,
      text,
      target_viewer_id: target.targetViewerId,
      target_persona_id: target.targetPersonaId
    });
    await acknowledgement;
  }

  listRoomMemories(roomId: string): Promise<RoomLongTermMemory[]> {
    return this.request(
      `/shared-brain/rooms/${encodeURIComponent(roomId)}/memories`,
      "GET"
    );
  }

  getRoomMemoryHead(roomId: string): Promise<RoomMemoryHead> {
    return this.request(
      `/shared-brain/rooms/${encodeURIComponent(roomId)}/memory-head`,
      "GET"
    );
  }

  editRoomMemory(
    roomId: string,
    memoryId: string,
    edit: RoomMemoryEdit
  ): Promise<RoomLongTermMemory> {
    return this.request(
      `/shared-brain/rooms/${encodeURIComponent(roomId)}/memories/${encodeURIComponent(memoryId)}`,
      "PUT",
      {
        expected_revision: edit.expectedRevision,
        content: edit.content,
        confidence: edit.confidence,
        evidence_event_ids: edit.evidenceEventIds
      }
    );
  }

  revokeRoomMemory(
    roomId: string,
    memoryId: string,
    expectedRevision: number
  ): Promise<RoomLongTermMemory> {
    return this.request(
      `/shared-brain/rooms/${encodeURIComponent(roomId)}/memories/${encodeURIComponent(memoryId)}/revoke`,
      "POST",
      { expected_revision: expectedRevision }
    );
  }

  async deleteRoomMemory(
    roomId: string,
    memoryId: string,
    expectedRevision: number
  ): Promise<void> {
    await this.request(
      `/shared-brain/rooms/${encodeURIComponent(roomId)}/memories/${encodeURIComponent(memoryId)}?expected_revision=${expectedRevision}`,
      "DELETE"
    );
  }

  resetRoomMemories(roomId: string, expectedRevision: number): Promise<MemoryResetResponse> {
    return this.request(
      `/shared-brain/rooms/${encodeURIComponent(roomId)}/memories/reset`,
      "POST",
      { expected_revision: expectedRevision }
    );
  }

  listModeMemes(namespaceId: string): Promise<ModeMeme[]> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/memes`,
      "GET"
    );
  }

  importLegacyMeme(
    namespaceId: string,
    legacy: LegacyMemeImportRequest
  ): Promise<LegacyMemeImportResponse> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/legacy-memes/import`,
      "POST",
      legacy
    );
  }

  listPendingMemeCandidates(namespaceId: string): Promise<MemeCandidate[]> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/meme-candidates/pending`,
      "GET"
    );
  }

  getModeMemeAutoIngest(namespaceId: string): Promise<AutoIngestResponse> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/auto-ingest`,
      "GET"
    );
  }

  setModeMemeAutoIngest(
    namespaceId: string,
    enabled: boolean,
    expectedRevision: number
  ): Promise<AutoIngestResponse> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/auto-ingest`,
      "PUT",
      { enabled, expected_revision: expectedRevision }
    );
  }

  approveMemeCandidate(
    namespaceId: string,
    candidateId: string
  ): Promise<CandidateCommitResponse> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/meme-candidates/${encodeURIComponent(candidateId)}/approve`,
      "POST"
    );
  }

  rejectMemeCandidate(
    namespaceId: string,
    candidateId: string
  ): Promise<MemeCandidate> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/meme-candidates/${encodeURIComponent(candidateId)}/reject`,
      "POST"
    );
  }

  mutateModeMeme(
    namespaceId: string,
    memeId: string,
    action: "undo" | "revoke" | "disable" | "restore" | "pin" | "unpin" | "archive" | "restart",
    expectedRevision: number
  ): Promise<ModeMeme> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/memes/${encodeURIComponent(memeId)}/${action}`,
      "POST",
      { expected_revision: expectedRevision }
    );
  }

  editModeMeme(
    namespaceId: string,
    memeId: string,
    edit: ModeMemeEdit
  ): Promise<ModeMeme> {
    return this.request(
      `/shared-brain/modes/${encodeURIComponent(namespaceId)}/memes/${encodeURIComponent(memeId)}`,
      "PUT",
      {
        text: edit.text,
        expected_revision: edit.expectedRevision,
        intensity: edit.intensity
      }
    );
  }

  async submitFrame(input: {
    inputId: string;
    capturedAtMs: number;
    mimeType: string;
    body: Uint8Array;
  }): Promise<void> {
    const sessionId = this.requireRunningSession();
    const acknowledgement = this.waitForIngest(input.inputId, "received");
    this.sendBinary(
      encodeBinaryEnvelope({
        mediaType: "image",
        sessionId,
        inputId: input.inputId,
        capturedAtMs: input.capturedAtMs,
        format: input.mimeType,
        body: input.body
      })
    );
    await acknowledgement;
  }

  submitAudioSegment(input: {
    inputId: string;
    capturedAtMs: number;
    body: Uint8Array;
  }): Promise<void> {
    const send = async (): Promise<void> => {
      const sessionId = this.requireRunningSession();
      const received = this.waitForIngest(input.inputId, "received");
      this.sendBinary(
        encodeBinaryEnvelope({
          mediaType: "audio",
          sessionId,
          inputId: input.inputId,
          capturedAtMs: input.capturedAtMs,
          format: "audio/pcm;rate=16000;channels=1;format=s16le",
          body: input.body
        })
      );
      await received;

      const committed = this.waitForIngest(input.inputId, "committed");
      this.sendJson({
        type: "client.audio.commit",
        protocol_version: PROTOCOL_VERSION,
        session_id: sessionId,
        input_id: input.inputId,
        committed_at_ms: Date.now()
      });
      await committed;
    };
    const queued = this.audioQueue.then(send, send);
    this.audioQueue = queued.catch(() => undefined);
    return queued;
  }

  private async sessionCommand(
    command: "pause" | "resume" | "stop"
  ): Promise<BackendSessionSnapshot> {
    const sessionId = this.session.sessionId;
    if (!sessionId) throw new BackendClientError("session_not_active", "没有活动中的直播 Session。");
    return this.applySession(
      await this.request<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/${command}`, "POST")
    );
  }

  private async refreshConfigurationStatus(): Promise<void> {
    const response = await this.request<{ configured: boolean }>("/configuration/providers", "GET");
    this.providersConfigured = response.configured;
    this.emitStatus();
  }

  private async refreshSessionStatus(): Promise<void> {
    this.applySession(await this.request<SessionSnapshot>("/sessions/current", "GET"));
  }

  private async request<T = unknown>(
    path: string,
    method: string,
    body?: object,
    options: RequestOptions = {}
  ): Promise<T> {
    this.requireLocalToken();
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${this.localToken}`,
          [PROTOCOL_HEADER]: String(PROTOCOL_VERSION),
          ...(body ? { "Content-Type": "application/json" } : {})
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(options.timeoutMs ?? CONNECT_TIMEOUT_MS)
      });
    } catch (error) {
      if (error instanceof Error && error.name === "TimeoutError") {
        throw new BackendClientError(
          options.timeoutCode ?? "backend_timeout",
          options.timeoutMessage ?? "本地后端响应超时。"
        );
      }
      throw new BackendClientError("backend_unavailable", "本地后端暂时不可用。");
    }
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        detail?: { code?: string; message?: string };
      } | null;
      throw new BackendClientError(
        payload?.detail?.code ?? `http_${response.status}`,
        payload?.detail?.message ?? "后端请求失败。"
      );
    }
    return (await response.json()) as T;
  }

  private async ensureConnected(): Promise<void> {
    this.requireLocalToken();
    if (this.socket?.readyState === WebSocket.OPEN && this.connection === "connected") return;
    if (this.connectPromise) return this.connectPromise;
    this.connectPromise = this.connect();
    try {
      await this.connectPromise;
    } finally {
      this.connectPromise = null;
    }
  }

  private connect(): Promise<void> {
    this.setConnection("connecting");
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(this.websocketUrl);
      this.socket = socket;
      let ready = false;
      const timeout = setTimeout(() => {
        if (ready) return;
        socket.close();
        reject(new BackendClientError("backend_timeout", "连接本地后端超时。"));
      }, CONNECT_TIMEOUT_MS);

      socket.addEventListener("open", () => {
        socket.send(
          JSON.stringify({
            type: "client.hello",
            protocol_version: PROTOCOL_VERSION,
            token: this.localToken
          })
        );
      });
      socket.addEventListener("message", (event) => {
        if (typeof event.data !== "string") return;
        const message = this.parseMessage(event.data);
        if (!message) {
          if (!ready && this.connection === "failed") {
            reject(
              new BackendClientError(
                "protocol_error",
                this.startupError ?? "后端实时消息不符合 protocol v2。"
              )
            );
          }
          return;
        }
        if (message.type === "backend.ready") {
          ready = true;
          clearTimeout(timeout);
          this.applySession(message.session);
          this.setConnection("connected");
          resolve();
          return;
        }
        this.handleMessage(message);
      });
      socket.addEventListener("error", () => {
        if (!ready) reject(new BackendClientError("backend_unavailable", "无法连接本地后端。"));
      });
      socket.addEventListener("close", () => {
        clearTimeout(timeout);
        if (this.socket === socket) this.socket = null;
        if (this.connection !== "failed") this.setConnection("disconnected");
        this.rejectPending(new BackendClientError("connection_closed", "后端连接已断开。"));
        if (!ready) reject(new BackendClientError("connection_closed", "后端连接已断开。"));
        if (this.connection !== "failed") this.scheduleReconnect();
      });
    });
  }

  private parseMessage(value: string): RealtimeServerMessage | null {
    try {
      return validateRealtimeServerMessage(JSON.parse(value));
    } catch (error) {
      this.failProtocol(error);
      return null;
    }
  }

  private handleMessage(value: unknown): void {
    let message: RealtimeServerMessage;
    try {
      message = validateRealtimeServerMessage(value);
    } catch (error) {
      this.failProtocol(error);
      return;
    }
    switch (message.type) {
      case "session.status":
        this.applySession(message.session);
        break;
      case "barrage.event": {
        if (
          message.barrage.expires_at_ms <= Date.now() ||
          message.barrage.session_id !== this.session.sessionId ||
          (this.runtime !== null &&
            message.barrage.audience_epoch !== this.runtime.audience_epoch)
        ) {
          break;
        }
        const event: BackendBarrageEvent = {
          barrageId: message.barrage.barrage_id,
          audienceId: message.barrage.viewer_instance_id,
          audienceName: message.barrage.display_name,
          text: message.barrage.text,
          createdAt: message.barrage.created_at_ms,
          roomId: message.barrage.room_id,
          sessionId: message.barrage.session_id,
          audienceEpoch: message.barrage.audience_epoch,
          observationId: message.barrage.observation_id,
          generationRequestId: message.barrage.generation_request_id,
          viewerInstanceId: message.barrage.viewer_instance_id,
          personaId: message.barrage.persona_id,
          viewerSequence: message.barrage.viewer_sequence,
          reactionType: message.barrage.reaction_type,
          evidenceRefs: (message.barrage.evidence_refs ?? []).map((reference) => ({
            source: reference.source,
            eventId: reference.event_id,
            frameIndex: reference.frame_index
          })),
          expiresAt: message.barrage.expires_at_ms
        };
        for (const listener of this.barrageListeners) listener(event);
        break;
      }
      case "ingest.ack":
        this.resolveIngest(message);
        break;
      case "ingest.rejected":
        this.rejectIngest(message);
        break;
      case "protocol.error":
        this.rejectPending(new BackendClientError(message.code, message.message));
        break;
    }
  }

  private failProtocol(error: unknown): void {
    const message =
      error instanceof Error && error.message
        ? error.message
        : "后端实时消息不符合 protocol v2。";
    const protocolError = new BackendClientError("protocol_error", message);
    this.startupError = message;
    this.rejectPending(protocolError);
    this.setConnection("failed");
    this.socket?.close(1002, "invalid protocol message");
  }

  private waitForIngest(inputId: string, stage: "received" | "committed"): Promise<void> {
    const key = ingestKey(inputId, stage);
    if (this.pendingIngest.has(key)) {
      return Promise.reject(new BackendClientError("duplicate_input", "输入正在等待后端确认。"));
    }
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pendingIngest.delete(key);
        reject(new BackendClientError("ingest_timeout", "后端没有及时确认输入。"));
      }, INGEST_ACK_TIMEOUT_MS);
      this.pendingIngest.set(key, { resolve, reject, timeout });
    });
  }

  private resolveIngest(message: RealtimeIngestAck): void {
    const pending = this.pendingIngest.get(ingestKey(message.input_id, message.stage));
    if (!pending) return;
    clearTimeout(pending.timeout);
    this.pendingIngest.delete(ingestKey(message.input_id, message.stage));
    pending.resolve();
  }

  private rejectIngest(message: RealtimeIngestRejected): void {
    if (!message.input_id) return;
    for (const stage of ["received", "committed"] as const) {
      const key = ingestKey(message.input_id, stage);
      const pending = this.pendingIngest.get(key);
      if (!pending) continue;
      clearTimeout(pending.timeout);
      this.pendingIngest.delete(key);
      pending.reject(new BackendClientError(message.code, message.message));
    }
  }

  private sendJson(message: object): void {
    this.requireSocket().send(JSON.stringify(message));
  }

  private sendBinary(message: Uint8Array): void {
    this.requireSocket().send(message);
  }

  private requireSocket(): WebSocket {
    if (this.socket?.readyState !== WebSocket.OPEN || this.connection !== "connected") {
      throw new BackendClientError("backend_disconnected", "后端实时连接尚未就绪。");
    }
    return this.socket;
  }

  private requireRunningSession(): string {
    if (this.session.state !== "running" || !this.session.sessionId) {
      throw new BackendClientError("session_not_running", "直播 Session 当前未运行。");
    }
    return this.session.sessionId;
  }

  private requireLocalToken(): void {
    if (!this.localToken) {
      throw new BackendClientError("local_token_missing", "桌面端没有收到本地后端令牌。");
    }
  }

  private applySession(snapshot: SessionSnapshot): BackendSessionSnapshot {
    if (
      snapshot.session_id &&
      snapshot.session_id === this.recoverableRuntimeSessionId &&
      (snapshot.state === "running" || snapshot.state === "paused")
    ) {
      this.recoverableRuntimeSessionId = null;
    }
    this.session = {
      sessionId: snapshot.session_id,
      state: snapshot.state,
      startedAtMs: snapshot.started_at_ms,
      updatedAtMs: snapshot.updated_at_ms,
      revision: snapshot.revision
    };
    this.emitStatus();
    return this.session;
  }

  private rememberRuntime(runtime: RuntimeQuerySnapshot): void {
    this.runtime = runtime;
    this.runtimeProvidersByRevision.set(
      runtimeProviderRevisionKey(runtime.session_id, runtime.config_revision),
      runtime.canonical_runtime_spec.provider
    );
  }

  private setConnection(connection: BackendConnectionState): void {
    if (this.connection === connection) return;
    this.connection = connection;
    this.emitStatus();
  }

  private snapshot(): BackendRuntimeStatus {
    return {
      connection: this.connection,
      providersConfigured: this.providersConfigured,
      startupError: this.startupError,
      recoverableRuntimeSessionId: this.recoverableRuntimeSessionId,
      session: { ...this.session }
    };
  }

  private emitStatus(): void {
    const status = this.snapshot();
    for (const listener of this.statusListeners) listener(status);
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pendingIngest.values()) {
      clearTimeout(pending.timeout);
      pending.reject(error);
    }
    this.pendingIngest.clear();
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.ensureConnected().catch(() => this.scheduleReconnect());
    }, 1_000);
  }
}

function sameRuntimeProvider(
  left: CanonicalRuntimeSpecProvider,
  right: CanonicalRuntimeSpecProvider
): boolean {
  return (
    left.provider_profile_id === right.provider_profile_id &&
    left.director_model === right.director_model &&
    left.viewer_model === right.viewer_model &&
    left.memory_model === right.memory_model &&
    left.visual_summary_model === right.visual_summary_model
  );
}

function sameRuntimeProviderCandidate(
  provider: CanonicalRuntimeSpecProvider,
  candidate: RuntimeModelProviderCandidate
): boolean {
  return (
    provider.provider_profile_id === candidate.provider_profile_id &&
    provider.director_model === (candidate.director_model ?? candidate.model_name) &&
    provider.viewer_model === (candidate.viewer_model ?? candidate.model_name) &&
    provider.memory_model === (candidate.memory_model ?? candidate.model_name) &&
    provider.visual_summary_model ===
      (candidate.visual_summary_model ?? candidate.model_name)
  );
}

type CanonicalRuntimeSpecProvider = CompiledRuntimeSpec["spec"]["provider"];

function validateRealtimeServerMessage(value: unknown): RealtimeServerMessage {
  const message = requireRecord(value, "实时消息必须是 JSON 对象");
  if (message.protocol_version !== PROTOCOL_VERSION) {
    throw new Error(
      `后端实时协议版本无效：需要 v${PROTOCOL_VERSION}，收到 ${String(message.protocol_version)}。`
    );
  }
  if (typeof message.type !== "string") {
    throw new Error("后端实时消息缺少 type。");
  }

  switch (message.type) {
    case "backend.ready":
    case "session.status":
      requireAllowedKeys(message, ["protocol_version", "type", "session"], message.type);
      validateSessionSnapshot(message.session);
      break;
    case "backend.pong":
      requireAllowedKeys(message, ["protocol_version", "type", "request_id"], message.type);
      requireString(message.request_id, "request_id");
      break;
    case "ingest.ack": {
      requireAllowedKeys(
        message,
        [
          "protocol_version",
          "type",
          "session_id",
          "input_id",
          "input_kind",
          "stage",
          "accepted_at_ms"
        ],
        message.type
      );
      requireBoundedString(message.session_id, "session_id", 128);
      requireBoundedString(message.input_id, "input_id", 128);
      requireEnumValue(message.input_kind, "input_kind", INGEST_INPUT_KINDS);
      requireEnumValue(message.stage, "stage", INGEST_ACK_STAGES);
      requireIntegerAtLeast(message.accepted_at_ms, "accepted_at_ms", 0);
      break;
    }
    case "ingest.rejected":
      requireAllowedKeys(
        message,
        [
          "protocol_version",
          "type",
          "code",
          "message",
          "session_id",
          "input_id",
          "input_kind"
        ],
        message.type
      );
      requireEnumValue(message.code, "code", INGEST_REJECTION_CODES);
      requireBoundedString(message.message, "message", 256);
      requireOptionalBoundedString(message.session_id, "session_id", 128);
      requireOptionalBoundedString(message.input_id, "input_id", 128);
      requireOptionalEnumValue(message.input_kind, "input_kind", INGEST_INPUT_KINDS);
      break;
    case "protocol.error":
      requireAllowedKeys(
        message,
        ["protocol_version", "type", "code", "message", "supported_version"],
        message.type
      );
      requireEnumValue(message.code, "code", REALTIME_PROTOCOL_ERROR_CODES);
      requireBoundedString(message.message, "message", 256);
      requireOptionalInteger(message.supported_version, "supported_version");
      break;
    case "barrage.event":
      requireAllowedKeys(message, ["protocol_version", "type", "barrage"], message.type);
      validateBarrage(message.barrage);
      break;
    default:
      throw new Error(`不支持的后端实时消息类型：${message.type}。`);
  }

  return value as RealtimeServerMessage;
}

function validateSessionSnapshot(value: unknown): void {
  const session = requireRecord(value, "session 必须是对象");
  if (session.session_id !== null) requireString(session.session_id, "session.session_id");
  requireEnumValue(session.state, "session.state", SESSION_STATES);
  if (session.started_at_ms !== null) {
    requireIntegerAtLeast(session.started_at_ms, "session.started_at_ms", 0);
  }
  requireIntegerAtLeast(session.updated_at_ms, "session.updated_at_ms", 0);
  requireIntegerAtLeast(session.revision, "session.revision", 0);
}

function validateBarrage(value: unknown): void {
  const barrage = requireRecord(value, "barrage 必须是对象");
  requireAllowedKeys(
    barrage,
    [
      "barrage_id",
      "room_id",
      "session_id",
      "audience_epoch",
      "observation_id",
      "generation_request_id",
      "viewer_instance_id",
      "persona_id",
      "display_name",
      "viewer_sequence",
      "reaction_type",
      "evidence_refs",
      "text",
      "created_at_ms",
      "expires_at_ms"
    ],
    "barrage"
  );
  for (const field of [
    "barrage_id",
    "room_id",
    "session_id",
    "observation_id",
    "generation_request_id",
    "viewer_instance_id",
    "persona_id"
  ]) {
    requireBoundedString(barrage[field], `barrage.${field}`, 128);
  }
  for (const field of ["display_name", "reaction_type"]) {
    requireBoundedString(barrage[field], `barrage.${field}`, 64);
  }
  requireBoundedString(barrage.text, "barrage.text", 200);
  requireIntegerAtLeast(barrage.audience_epoch, "barrage.audience_epoch", 1);
  requireIntegerAtLeast(barrage.viewer_sequence, "barrage.viewer_sequence", 1);
  requireIntegerAtLeast(barrage.created_at_ms, "barrage.created_at_ms", 0);
  requireIntegerAtLeast(barrage.expires_at_ms, "barrage.expires_at_ms", 1);
  if ((barrage.expires_at_ms as number) <= (barrage.created_at_ms as number)) {
    throw new Error("barrage.expires_at_ms 必须晚于 barrage.created_at_ms。");
  }
  if (barrage.evidence_refs !== undefined) {
    if (!Array.isArray(barrage.evidence_refs) || barrage.evidence_refs.length > 128) {
      throw new Error("barrage.evidence_refs 必须是数组。");
    }
    for (const value of barrage.evidence_refs) {
      const reference = requireRecord(value, "barrage.evidence_refs 项必须是对象");
      requireAllowedKeys(
        reference,
        ["source", "event_id", "frame_index"],
        "barrage.evidence_refs 项"
      );
      if (reference.source !== "event" && reference.source !== "frame") {
        throw new Error("barrage.evidence_refs.source 无效。");
      }
      if (reference.source === "event") {
        requireBoundedString(reference.event_id, "barrage.evidence_refs.event_id", 128);
        if (reference.frame_index !== undefined && reference.frame_index !== null) {
          throw new Error("event 证据只能包含 event_id。");
        }
      } else {
        requireIntegerAtLeast(reference.frame_index, "barrage.evidence_refs.frame_index", 0);
        if (reference.event_id !== undefined && reference.event_id !== null) {
          throw new Error("frame 证据只能包含 frame_index。");
        }
      }
    }
  }
}

function requireRecord(value: unknown, message: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function requireAllowedKeys(
  value: Record<string, unknown>,
  allowedKeys: readonly string[],
  field: string
): void {
  const allowed = new Set(allowedKeys);
  const unexpected = Object.keys(value).find((key) => !allowed.has(key));
  if (unexpected) {
    throw new Error(`${field} 包含合同未定义的字段 ${unexpected}。`);
  }
}

function requireString(value: unknown, field: string): void {
  if (typeof value !== "string") {
    throw new Error(`${field} 缺失或无效。`);
  }
}

function requireNonEmptyString(value: unknown, field: string): void {
  requireString(value, field);
  if (!(value as string)) {
    throw new Error(`${field} 缺失或无效。`);
  }
}

function requireBoundedString(value: unknown, field: string, maxLength: number): void {
  requireNonEmptyString(value, field);
  if (Array.from(value as string).length > maxLength) {
    throw new Error(`${field} 长度不能超过 ${maxLength}。`);
  }
}

function requireFiniteNumber(value: unknown, field: string): void {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${field} 缺失或无效。`);
  }
}

function requireIntegerAtLeast(value: unknown, field: string, minimum: number): void {
  requireFiniteNumber(value, field);
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new Error(`${field} 必须是大于或等于 ${minimum} 的整数。`);
  }
}

function requireOptionalInteger(value: unknown, field: string): void {
  if (value === undefined || value === null) return;
  requireFiniteNumber(value, field);
  if (!Number.isInteger(value)) {
    throw new Error(`${field} 必须是整数。`);
  }
}

function requireOptionalBoundedString(
  value: unknown,
  field: string,
  maxLength: number
): void {
  if (value === undefined || value === null) return;
  requireBoundedString(value, field, maxLength);
}

function requireEnumValue(
  value: unknown,
  field: string,
  allowedValues: ReadonlySet<string>
): void {
  if (typeof value !== "string" || !allowedValues.has(value)) {
    throw new Error(`${field} 不是合同允许的枚举值。`);
  }
}

function requireOptionalEnumValue(
  value: unknown,
  field: string,
  allowedValues: ReadonlySet<string>
): void {
  if (value === undefined || value === null) return;
  requireEnumValue(value, field, allowedValues);
}

const SESSION_STATES = new Set([
  "idle",
  "starting",
  "running",
  "paused",
  "stopping",
  "error"
]);
const INGEST_INPUT_KINDS = new Set(["text", "audio", "frame"]);
const INGEST_ACK_STAGES = new Set(["received", "committed"]);
const INGEST_REJECTION_CODES = new Set([
  "invalid_input",
  "session_not_active",
  "duplicate_input",
  "unknown_input",
  "out_of_order",
  "payload_too_large",
  "unsupported_format",
  "unsupported_binary_version",
  "unsupported_media_type",
  "malformed_binary_envelope",
  "pipeline_unavailable"
]);
const REALTIME_PROTOCOL_ERROR_CODES = new Set([
  "invalid_message",
  "authentication_failed",
  "version_mismatch",
  "handshake_timeout",
  "message_too_large",
  "unexpected_message"
]);

function idleSession(): BackendSessionSnapshot {
  return {
    sessionId: null,
    state: "idle",
    startedAtMs: null,
    updatedAtMs: 0,
    revision: 0
  };
}

function ingestKey(inputId: string, stage: "received" | "committed"): string {
  return `${inputId}:${stage}`;
}

function runtimeProviderRevisionKey(sessionId: string, revision: number): string {
  return `${sessionId}:${revision}`;
}
