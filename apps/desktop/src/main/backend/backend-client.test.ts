import { describe, expect, it } from "vitest";
import { BackendClient } from "./backend-client";

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
