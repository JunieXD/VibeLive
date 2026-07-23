import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { resolve } from "node:path";

const useShell = process.platform === "win32";
const localToken = process.env.ADVX_LOCAL_TOKEN ?? randomBytes(32).toString("base64url");
const configuredBackendUrl = process.env.ADVX_BACKEND_URL;
const backendPort = configuredBackendUrl ? null : await findAvailablePort();
const backendUrl = configuredBackendUrl ?? `http://127.0.0.1:${backendPort}`;
const childEnvironment = {
  ...process.env,
  ADVX_BACKEND_EXTERNAL: "1",
  ADVX_BACKEND_URL: backendUrl,
  ADVX_DATA_DIR: resolve(".advx-data"),
  ADVX_LOCAL_TOKEN: localToken
};
const children = [];
let shuttingDown = false;
let backendExited = false;

if (!configuredBackendUrl) {
  const backend = spawn(
    "uv",
    [
      "run",
      "--project",
      "apps/backend",
      "uvicorn",
      "advx_backend.main:app",
      "--app-dir",
      "apps/backend/src",
      "--reload",
      "--host",
      "127.0.0.1",
      "--port",
      String(backendPort)
    ],
    { stdio: "inherit", shell: useShell, env: childEnvironment }
  );
  children.push(backend);
  backend.on("exit", () => {
    backendExited = true;
  });
  observeChild(backend, "Backend");
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

try {
  console.log(`Waiting for ADVX backend at ${backendUrl}...`);
  await waitForBackendHealth(backendUrl);
  if (backendExited) throw new Error("Backend exited immediately after becoming ready.");
  console.log("Backend ready; starting Electron.");

  const desktop = spawn("pnpm", ["--filter", "@advx/desktop", "dev"], {
    stdio: "inherit",
    shell: useShell,
    env: childEnvironment
  });
  children.push(desktop);
  observeChild(desktop, "Desktop");
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  shutdown(1);
}

function observeChild(child, label) {
  child.on("error", (error) => {
    console.error(`${label}: ${error.message}`);
    shutdown(1);
  });
  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    if (signal) console.error(`${label} stopped by ${signal}.`);
    shutdown(code ?? 1);
  });
}

function shutdown(exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (!child.killed) child.kill();
  }
  process.exitCode = exitCode;
}

async function findAvailablePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Unable to allocate a local backend port."));
        return;
      }
      const { port } = address;
      server.close((error) => (error ? reject(error) : resolvePort(port)));
    });
  });
}

async function waitForBackendHealth(baseUrl) {
  const deadline = Date.now() + 15_000;
  let lastError = null;
  while (Date.now() < deadline) {
    if (backendExited) throw new Error("Backend exited before it became ready.");
    try {
      const response = await fetch(`${baseUrl}/health`, {
        signal: AbortSignal.timeout(1_000)
      });
      if (response.ok) {
        const payload = await response.json();
        if (payload.status === "ok" && payload.protocol_version === 1) return;
        lastError = new Error("Backend health response uses an incompatible protocol.");
      }
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  const detail = lastError instanceof Error ? ` ${lastError.message}` : "";
  throw new Error(`Backend did not become ready within 15 seconds.${detail}`);
}
