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
  ModelConfig
} from "../../shared/contracts";
import { encodeBinaryEnvelope } from "./realtime-binary";

const PROTOCOL_VERSION = 1;
const PROTOCOL_HEADER = "X-ADVX-Protocol-Version";
const INGEST_ACK_TIMEOUT_MS = 10_000;
const CONNECT_TIMEOUT_MS = 8_000;

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
      model_name: config.model,
      model_api_key: config.apiKey,
      asr_api_key: config.asrApiKey
    });
    this.providersConfigured = true;
    this.emitStatus();
  }

  async startSession(): Promise<BackendSessionSnapshot> {
    await this.ensureConnected();
    await this.refreshConfigurationStatus();
    if (!this.providersConfigured) {
      throw new BackendClientError(
        "providers_not_configured",
        "请先在设置中保存模型和语音识别配置。"
      );
    }
    return this.applySession(await this.request<SessionSnapshot>("/sessions", "POST"));
  }

  async pauseSession(): Promise<BackendSessionSnapshot> {
    return this.sessionCommand("pause");
  }

  async resumeSession(): Promise<BackendSessionSnapshot> {
    return this.sessionCommand("resume");
  }

  async stopSession(): Promise<BackendSessionSnapshot> {
    return this.sessionCommand("stop");
  }

  async submitText(inputId: string, createdAtMs: number, text: string): Promise<void> {
    const sessionId = this.requireRunningSession();
    const acknowledgement = this.waitForIngest(inputId, "received");
    this.sendJson({
      type: "client.text.submit",
      protocol_version: PROTOCOL_VERSION,
      session_id: sessionId,
      input_id: inputId,
      created_at_ms: createdAtMs,
      text
    });
    await acknowledgement;
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

  private async request<T = unknown>(path: string, method: string, body?: object): Promise<T> {
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
        signal: AbortSignal.timeout(CONNECT_TIMEOUT_MS)
      });
    } catch {
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
        if (!message) return;
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
        this.setConnection("disconnected");
        this.rejectPending(new BackendClientError("connection_closed", "后端连接已断开。"));
        if (!ready) reject(new BackendClientError("connection_closed", "后端连接已断开。"));
        this.scheduleReconnect();
      });
    });
  }

  private parseMessage(value: string): RealtimeServerMessage | null {
    try {
      return JSON.parse(value) as RealtimeServerMessage;
    } catch {
      return null;
    }
  }

  private handleMessage(message: RealtimeServerMessage): void {
    switch (message.type) {
      case "session.status":
        this.applySession(message.session);
        break;
      case "barrage.event": {
        const event: BackendBarrageEvent = {
          barrageId: message.barrage.barrage_id,
          audienceId: message.barrage.audience_id,
          text: message.barrage.text,
          createdAt: message.barrage.created_at_ms
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
