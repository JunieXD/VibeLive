import { describe, expect, it, vi } from "vitest";
import {
  ExternalBackendProcess,
  SpawnedBackendProcess,
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
  it("waits until the protocol v2 health endpoint is ready", async () => {
    let now = 0;
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("connection refused"))
      .mockResolvedValueOnce(healthResponse({ status: "ok", protocol_version: 2 }));

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
    const controller = new SpawnedBackendProcess({
      command: "advx-definitely-missing-backend-executable",
      cwd: process.cwd(),
      env: process.env,
      baseUrl: "http://127.0.0.1:9",
      startupTimeoutMs: 1_000
    });

    await expect(controller.start()).rejects.toThrow("无法启动本地后端进程");
    expect(controller.process).toBeNull();
  });
});
