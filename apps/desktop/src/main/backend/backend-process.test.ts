import { describe, expect, it, vi } from "vitest";
import {
  createBackendOutputForwarder,
  ExternalBackendProcess,
  SpawnedBackendProcess,
  forwardBackendOutput,
  waitForBackendHealth
} from "./backend-process";

function healthResponse(payload: object, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => payload
  } as Response;
}

describe("backend process readiness", () => {
  it("waits until the protocol v3 health endpoint is ready", async () => {
    let now = 0;
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("connection refused"))
      .mockResolvedValueOnce(healthResponse({ status: "ok", protocol_version: 3 }));

    await waitForBackendHealth({
      baseUrl: "http://127.0.0.1:8765/",
      timeoutMs: 1_000,
      intervalMs: 25,
      fetchImpl,
      now: () => now,
      sleep: async (milliseconds) => {
        now += milliseconds;
      }
    });

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[1][0]).toBe("http://127.0.0.1:8765/health");
  });

  it("rejects protocol v1 health responses at the deadline", async () => {
    let now = 0;
    await expect(
      waitForBackendHealth({
        baseUrl: "http://127.0.0.1:8765",
        timeoutMs: 50,
        intervalMs: 25,
        fetchImpl: vi
          .fn<typeof fetch>()
          .mockResolvedValue(healthResponse({ status: "ok", protocol_version: 1 })),
        now: () => now,
        sleep: async (milliseconds) => {
          now += milliseconds;
        }
      })
    ).rejects.toThrow("不兼容")
  });

  it("surfaces a child startup error without waiting for timeout", async () => {
    await expect(
      waitForBackendHealth({
        baseUrl: "http://127.0.0.1:8765",
        getStartupError: () => new Error("backend exited")
      })
    ).rejects.toThrow("backend exited")
  });

  it("uses health readiness for externally managed backends", async () => {
    const controller = new ExternalBackendProcess({
      baseUrl: "http://127.0.0.1:8765",
      startupTimeoutMs: 1
    });
    expect(controller.process).toBeNull();
    expect(controller.onUnexpectedExit(() => undefined)).toBeTypeOf("function");
    await controller.stop();
  });

  it("surfaces a missing managed backend executable", async () => {
    const logger = {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn()
    };
    const controller = new SpawnedBackendProcess({
      command: "advx-definitely-missing-backend-executable",
      cwd: process.cwd(),
      env: process.env,
      baseUrl: "http://127.0.0.1:9",
      logger,
      startupTimeoutMs: 1_000
    });

    await expect(controller.start()).rejects.toThrow("无法启动本地后端进程");
    expect(controller.process).toBeNull();
    expect(logger.error).toHaveBeenCalledWith(
      "backend.spawn.failed",
      expect.any(Error)
    );
  });

  it("routes backend output through the injected logger", () => {
    const logger = {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn()
    };

    forwardBackendOutput(logger, "stdout", Buffer.from("server ready\n"));
    forwardBackendOutput(logger, "stderr", Buffer.from("INFO: request complete\n"));
    forwardBackendOutput(logger, "stderr", Buffer.from("ERROR: worker crashed\n"));

    expect(logger.info).toHaveBeenCalledWith("backend.output", {
      stream: "stdout",
      text: "server ready"
    });
    expect(logger.info).toHaveBeenCalledWith("backend.output", {
      stream: "stderr",
      text: "INFO: request complete"
    });
    expect(logger.error).toHaveBeenCalledWith("backend.output", {
      stream: "stderr",
      text: "ERROR: worker crashed"
    });
  });

  it("buffers split lines before redacting backend secrets", () => {
    const logger = {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn()
    };
    const forwarder = createBackendOutputForwarder(logger, "stderr");

    forwarder.write(Buffer.from('ERROR: provider failed {"api_key":"plain-'));
    forwarder.write(Buffer.from('provider-secret"}\n'));
    forwarder.flush();

    expect(logger.error).toHaveBeenCalledOnce();
    expect(logger.error).toHaveBeenCalledWith("backend.output", {
      stream: "stderr",
      text: 'ERROR: provider failed {"api_key":[REDACTED]}'
    });
    expect(JSON.stringify(logger.error.mock.calls)).not.toContain("plain-provider-secret");
  });
});
