import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  clearRuntimeSessionId,
  loadRuntimeSessionId,
  saveRuntimeSessionId
} from "./runtime-session-state";

const createdDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(createdDirectories.splice(0).map((path) => rm(path, {
    recursive: true,
    force: true
  })));
});

describe("runtime session state", () => {
  it("persists only the last runtime session ID and clears it after stop", async () => {
    const directory = await mkdtemp(join(tmpdir(), "advx-runtime-session-"));
    createdDirectories.push(directory);

    await saveRuntimeSessionId(directory, " session-1 ");

    expect(await loadRuntimeSessionId(directory)).toBe("session-1");
    expect(JSON.parse(await readFile(join(directory, "runtime-session.json"), "utf8")))
      .toEqual({ sessionId: "session-1" });

    await clearRuntimeSessionId(directory);
    expect(await loadRuntimeSessionId(directory)).toBeNull();
  });
});
