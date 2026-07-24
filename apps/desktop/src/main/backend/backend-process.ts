import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import { redactLogText } from "../logging-redaction";

const DEFAULT_STARTUP_TIMEOUT_MS = 15_000;
const DEFAULT_HEALTH_INTERVAL_MS = 100;
const STOP_TIMEOUT_MS = 3_000;
const BACKEND_PROTOCOL_VERSION = 3;
const MAX_BACKEND_OUTPUT_LINE_BYTES = 64 * 1024;

export type BackendProcessExit = {
  code: number | null;
  signal: NodeJS.Signals | null;
};

export interface BackendProcessLogger {
  info(message: string, ...data: unknown[]): void;
  warn(message: string, ...data: unknown[]): void;
  error(message: string, ...data: unknown[]): void;
}

export interface BackendProcessController {
  readonly process: ChildProcessWithoutNullStreams | null;
  start(): Promise<void>;
  restart(): Promise<void>;
  stop(): Promise<void>;
  onUnexpectedExit(listener: (exit: BackendProcessExit) => void): () => void;
}

export type BackendHealthOptions = {
  baseUrl: string;
  timeoutMs?: number;
  intervalMs?: number;
  fetchImpl?: typeof fetch;
  now?: () => number;
  sleep?: (milliseconds: number) => Promise<void>;
  getStartupError?: () => Error | null;
};

export async function waitForBackendHealth(options: BackendHealthOptions): Promise<void> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_STARTUP_TIMEOUT_MS;
  const intervalMs = options.intervalMs ?? DEFAULT_HEALTH_INTERVAL_MS;
  const fetchImpl = options.fetchImpl ?? fetch;
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? ((milliseconds) => delay(milliseconds));
  const deadline = now() + timeoutMs;
  let lastError: unknown = null;

  while (now() < deadline) {
    const startupError = options.getStartupError?.();
    if (startupError) throw startupError;
    try {
      const response = await fetchImpl(`${options.baseUrl.replace(/\/$/, "")}/health`, {
        signal: AbortSignal.timeout(Math.min(1_000, Math.max(100, timeoutMs)))
      });
      if (response.ok) {
        const payload = (await response.json()) as {
          status?: unknown;
          protocol_version?: unknown;
        };
        if (
          payload.status === "ok" &&
          payload.protocol_version === BACKEND_PROTOCOL_VERSION
        ) {
          return;
        }
        lastError = new Error("本地后端返回了不兼容的健康状态。");
      } else {
        lastError = new Error(`本地后端健康检查返回 HTTP ${response.status}。`);
      }
    } catch (error) {
      lastError = error;
    }
    await sleep(intervalMs);
  }

  const suffix = lastError instanceof Error && lastError.message ? ` ${lastError.message}` : "";
  throw new Error(`本地后端没有在 ${Math.ceil(timeoutMs / 1_000)} 秒内启动。${suffix}`);
}

export class ExternalBackendProcess implements BackendProcessController {
  readonly process = null;
  private readonly baseUrl: string;
  private readonly startupTimeoutMs: number;

  constructor(options: { baseUrl: string; startupTimeoutMs?: number }) {
    this.baseUrl = options.baseUrl;
    this.startupTimeoutMs = options.startupTimeoutMs ?? DEFAULT_STARTUP_TIMEOUT_MS;
  }

  async start(): Promise<void> {
    await waitForBackendHealth({
      baseUrl: this.baseUrl,
      timeoutMs: this.startupTimeoutMs
    });
  }

  async restart(): Promise<void> {
    await this.start();
  }

  async stop(): Promise<void> {}

  onUnexpectedExit(_listener: (exit: BackendProcessExit) => void): () => void {
    return () => undefined;
  }
}

export type SpawnedBackendProcessOptions = {
  command: string;
  args?: readonly string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  baseUrl: string;
  logger?: BackendProcessLogger;
  startupTimeoutMs?: number;
};

export class SpawnedBackendProcess implements BackendProcessController {
  private readonly options: SpawnedBackendProcessOptions;
  private child: ChildProcessWithoutNullStreams | null = null;
  private startPromise: Promise<void> | null = null;
  private stopping = false;
  private ready = false;
  private startupError: Error | null = null;
  private readonly exitListeners = new Set<(exit: BackendProcessExit) => void>();

  constructor(options: SpawnedBackendProcessOptions) {
    this.options = options;
  }

  get process(): ChildProcessWithoutNullStreams | null {
    return this.child;
  }

  start(): Promise<void> {
    if (this.ready && this.child && this.child.exitCode === null) return Promise.resolve();
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.startChild().finally(() => {
      this.startPromise = null;
    });
    return this.startPromise;
  }

  async restart(): Promise<void> {
    await this.stop();
    await this.start();
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.ready = false;
    const child = this.child;
    this.child = null;
    if (
      !child ||
      child.exitCode !== null ||
      child.signalCode !== null ||
      child.pid === undefined
    ) {
      this.stopping = false;
      return;
    }

    this.options.logger?.info("backend.stop.requested", { pid: child.pid });
    const exited = onceTermination(child);
    await terminateChildProcess(child, false);
    await Promise.race([exited, delay(STOP_TIMEOUT_MS)]);
    if (child.exitCode === null && child.signalCode === null) {
      this.options.logger?.warn("backend.stop.forced", { pid: child.pid });
      await terminateChildProcess(child, true);
      await Promise.race([exited, delay(500)]);
    }
    this.stopping = false;
  }

  onUnexpectedExit(listener: (exit: BackendProcessExit) => void): () => void {
    this.exitListeners.add(listener);
    return () => this.exitListeners.delete(listener);
  }

  private async startChild(): Promise<void> {
    this.stopping = false;
    this.ready = false;
    this.startupError = null;
    const child = spawn(this.options.command, [...(this.options.args ?? [])], {
      cwd: this.options.cwd,
      env: this.options.env
    });
    this.child = child;
    this.options.logger?.info("backend.spawn.requested", {
      args: this.options.args ?? [],
      command: this.options.command,
      cwd: this.options.cwd
    });
    child.once("spawn", () => {
      this.options.logger?.info("backend.spawned", { pid: child.pid });
    });
    const stdoutForwarder = createBackendOutputForwarder(this.options.logger, "stdout");
    const stderrForwarder = createBackendOutputForwarder(this.options.logger, "stderr");
    child.stdout.on("data", stdoutForwarder.write);
    child.stdout.once("end", stdoutForwarder.flush);
    child.stderr.on("data", stderrForwarder.write);
    child.stderr.once("end", stderrForwarder.flush);
    child.once("error", (error) => {
      this.options.logger?.error("backend.spawn.failed", error);
      this.startupError = new Error(`无法启动本地后端进程：${error.message}`);
    });
    child.once("exit", (code, signal) => {
      if (this.child === child) this.child = null;
      const wasReady = this.ready;
      this.ready = false;
      const exit = { code, signal };
      if (this.stopping) this.options.logger?.info("backend.exit.expected", exit);
      else this.options.logger?.warn("backend.exit.unexpected", exit);
      if (!wasReady && !this.stopping) {
        this.startupError = new Error(
          `本地后端在启动期间退出（${describeExit(exit)}）。`
        );
      }
      if (wasReady && !this.stopping) {
        for (const listener of this.exitListeners) listener(exit);
      }
    });

    try {
      await waitForBackendHealth({
        baseUrl: this.options.baseUrl,
        timeoutMs: this.options.startupTimeoutMs,
        getStartupError: () => this.startupError
      });
      if (this.child !== child || child.exitCode !== null) {
        throw this.startupError ?? new Error("本地后端在健康检查后退出。");
      }
      this.ready = true;
      this.options.logger?.info("backend.ready", {
        baseUrl: this.options.baseUrl,
        pid: child.pid
      });
    } catch (error) {
      this.options.logger?.error("backend.start.failed", error);
      await this.stop();
      throw error;
    }
  }
}

export function forwardBackendOutput(
  logger: BackendProcessLogger | undefined,
  stream: "stdout" | "stderr",
  chunk: Buffer
): void {
  const forwarder = createBackendOutputForwarder(logger, stream);
  forwarder.write(chunk);
  forwarder.flush();
}

export function createBackendOutputForwarder(
  logger: BackendProcessLogger | undefined,
  stream: "stdout" | "stderr"
): { write(chunk: Buffer): void; flush(): void } {
  const decoder = new StringDecoder("utf8");
  let buffered = "";
  let droppingOversizedLine = false;

  const reportOversizedLine = (byteLength: number): void => {
    if (logger) {
      logger.warn("backend.output.omitted", {
        byteLength,
        reason: "line-too-long",
        stream
      });
      return;
    }
    process.stderr.write(
      `[backend] output omitted (${stream} line exceeded ${MAX_BACKEND_OUTPUT_LINE_BYTES} bytes)\n`
    );
  };

  const emitLine = (line: string): void => {
    const text = line.trimEnd();
    if (!text) return;
    const byteLength = Buffer.byteLength(text);
    if (byteLength > MAX_BACKEND_OUTPUT_LINE_BYTES) {
      reportOversizedLine(byteLength);
      return;
    }

    const redactedText = redactLogText(text);
    if (!logger) {
      const output = stream === "stdout" ? process.stdout : process.stderr;
      output.write(`[backend] ${redactedText}\n`);
      return;
    }
    const isError =
      stream === "stderr" && /\b(ERROR|CRITICAL)\b|Traceback|Exception/.test(redactedText);
    const write = isError ? logger.error.bind(logger) : logger.info.bind(logger);
    write("backend.output", { stream, text: redactedText });
  };

  const append = (decoded: string): void => {
    let incoming = decoded;
    if (droppingOversizedLine) {
      const lineEnd = /\r?\n/.exec(incoming);
      if (!lineEnd) return;
      incoming = incoming.slice(lineEnd.index + lineEnd[0].length);
      droppingOversizedLine = false;
    }

    buffered += incoming;
    const lines = buffered.split(/\r?\n/);
    buffered = lines.pop() ?? "";
    for (const line of lines) emitLine(line);
    if (Buffer.byteLength(buffered) > MAX_BACKEND_OUTPUT_LINE_BYTES) {
      reportOversizedLine(Buffer.byteLength(buffered));
      buffered = "";
      droppingOversizedLine = true;
    }
  };

  return {
    write: (chunk) => append(decoder.write(chunk)),
    flush: () => {
      append(decoder.end());
      if (!droppingOversizedLine && buffered) emitLine(buffered);
      buffered = "";
      droppingOversizedLine = false;
    }
  };
}

function describeExit(exit: BackendProcessExit): string {
  if (exit.signal) return `signal ${exit.signal}`;
  return `exit ${exit.code ?? "unknown"}`;
}

function onceTermination(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    child.once("exit", () => resolve());
    child.once("close", () => resolve());
  });
}

async function terminateChildProcess(
  child: ChildProcessWithoutNullStreams,
  force: boolean
): Promise<void> {
  if (process.platform !== "win32" || child.pid === undefined) {
    child.kill(force ? "SIGKILL" : "SIGTERM");
    return;
  }

  await new Promise<void>((resolve) => {
    const taskkill = spawn(
      "taskkill.exe",
      ["/pid", String(child.pid), "/t", ...(force ? ["/f"] : [])],
      { stdio: "ignore", windowsHide: true }
    );
    taskkill.once("error", () => {
      child.kill(force ? "SIGKILL" : "SIGTERM");
      resolve();
    });
    taskkill.once("exit", () => resolve());
  });
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
