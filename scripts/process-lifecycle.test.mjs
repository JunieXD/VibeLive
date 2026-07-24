import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const lifecycleModuleUrl = pathToFileURL(resolve("scripts/process-lifecycle.mjs")).href;

test("clears a fallback timer after completion", async () => {
  const child = spawn(
    process.execPath,
    [
      "--input-type=module",
      "--eval",
      `import { waitForCompletionOrTimeout } from ${JSON.stringify(lifecycleModuleUrl)}; await waitForCompletionOrTimeout(Promise.resolve(), 5_000);`
    ],
    { stdio: "ignore" }
  );
  const startedAt = performance.now();
  const [code, signal] = await once(child, "exit");
  const elapsedMs = performance.now() - startedAt;

  assert.equal(code, 0);
  assert.equal(signal, null);
  assert.ok(elapsedMs < 1_000, `fallback timer kept the process alive for ${elapsedMs}ms`);
});
