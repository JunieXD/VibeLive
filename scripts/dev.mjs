import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { resolve } from "node:path";
import {
  requestShutdownViaSocket,
  terminateWithFallback,
  waitForCompletionOrTimeout
} from "./process-lifecycle.mjs";

const useProcessGroups = process.platform !== "win32";
const backendProtocolVersion = 3;
const shutdownGraceMs = 5_000;
const localToken = process.env.ADVX_LOCAL_TOKEN ?? randomBytes(32).toString("base64url");
const configuredBackendUrl = process.env.ADVX_BACKEND_URL;
const backendPort = configuredBackendUrl ? null : await findAvailablePort();
const backendUrl = configuredBackendUrl ?? `http://127.0.0.1:${backendPort}`;
const desktopShutdownSocket =
  process.platform === "win32"
    ? `\\\\.\\pipe\\advx-live-${randomBytes(12).toString("hex")}`
    : `/tmp/advx-live-${randomBytes(12).toString("hex")}.sock`;
const {
  ELECTRON_RUN_AS_NODE: _electronRunAsNode,
  ...inheritedEnvironment
} = process.env;
const childEnvironment = {
  ...inheritedEnvironment,
  ADVX_BACKEND_EXTERNAL: "1",
  ADVX_BACKEND_URL: backendUrl,
  ADVX_DATA_DIR: resolve(".advx-data"),
  ADVX_LOCAL_TOKEN: localToken,
  ADVX_DESKTOP_SHUTDOWN_SOCKET: desktopShutdownSocket
};
let backendChild = null;
let desktopChild = null;
let shuttingDown = false;
let backendExited = false;
let shutdownPromise = null;

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
    {
      stdio: ["ignore", "inherit", "inherit"],
      detached: useProcessGroups,
      env: childEnvironment
    }
  );
  backendChild = backend;
  backend.on("exit", () => {
    backendExited = true;
  });
  observeChild(backend, "Backend");
}

process.on("SIGINT", () => void shutdown(0));
process.on("SIGTERM", () => void shutdown(0));

try {
  console.log(`Waiting for ADVX backend at ${backendUrl}...`);
  await waitForBackendHealth(backendUrl);
  if (backendExited) throw new Error("Backend exited immediately after becoming ready.");
  console.log("Backend ready; starting Electron.");

  const desktopCommand = resolvePnpmCommand(["--filter", "@advx/desktop", "dev"]);
  const desktop = spawn(desktopCommand.executable, desktopCommand.arguments, {
    stdio: ["ignore", "inherit", "inherit"],
    shell: desktopCommand.useShell,
    detached: useProcessGroups,
    env: childEnvironment
  });
  desktopChild = desktop;
  observeChild(desktop, "Desktop");
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  void shutdown(1);
}

function observeChild(child, label) {
  child.on("error", (error) => {
    console.error(`${label}: ${error.message}`);
    void shutdown(1);
  });
  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    if (signal) console.error(`${label} stopped by ${signal}.`);
    void shutdown(code ?? 1);
  });
}

function resolvePnpmCommand(arguments_) {
  return {
    executable: "pnpm",
    arguments: arguments_,
    useShell: process.platform === "win32"
  };
}

function shutdown(exitCode = 0) {
  if (shutdownPromise) return shutdownPromise;
  shuttingDown = true;
  shutdownPromise = (async () => {
    await stopDesktopChild();
    await stopChildTree(backendChild, "Backend");
    process.exitCode = exitCode;
  })();
  return shutdownPromise;
}

async function stopDesktopChild() {
  const child = desktopChild;
  if (!child || !isChildTreeRunning(child)) return;

  console.log("Requesting graceful Desktop shutdown...");
  const accepted = await requestShutdownViaSocket(desktopShutdownSocket);
  if (!accepted) {
    console.warn("Desktop shutdown control was unavailable; falling back to SIGTERM.");
    await stopChildTree(child, "Desktop");
    return;
  }

  await waitForCompletionOrTimeout(waitForChildTreeExit(child), shutdownGraceMs);
  if (!isChildTreeRunning(child)) return;

  console.error(`Desktop did not exit within ${shutdownGraceMs}ms; falling back to SIGTERM.`);
  await stopChildTree(child, "Desktop", 1_000);
}

async function stopChildTree(child, label, gracefulTimeoutMs = shutdownGraceMs) {
  if (!child || !isChildTreeRunning(child)) return;
  console.log(`Requesting graceful ${label} shutdown...`);
  await terminateWithFallback({
    isRunning: () => isChildTreeRunning(child),
    requestTermination: (signal) => terminateChildTree(child, signal),
    waitForExit: () => waitForChildTreeExit(child),
    gracefulTimeoutMs,
    forceTimeoutMs: 1_000,
    onForce: () =>
      console.error(`${label} did not exit within ${shutdownGraceMs}ms; forcing termination.`)
  });
}

function isRunning(child) {
  return child.exitCode === null && child.signalCode === null;
}

function isChildTreeRunning(child) {
  if (!useProcessGroups || child.pid === undefined) return isRunning(child);
  try {
    process.kill(-child.pid, 0);
    return true;
  } catch (error) {
    return error?.code !== "ESRCH";
  }
}

function terminateChildTree(child, signal) {
  if (!isChildTreeRunning(child) || child.pid === undefined) return;
  try {
    if (useProcessGroups) {
      process.kill(-child.pid, signal);
      return;
    }
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true
    });
    killer.unref();
  } catch (error) {
    if (error?.code !== "ESRCH") {
      console.error(`Failed to stop child process ${child.pid}: ${error}`);
    }
  }
}

async function waitForChildTreeExit(child) {
  while (isChildTreeRunning(child)) await delay(50);
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
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
        if (
          payload.status === "ok" &&
          payload.protocol_version === backendProtocolVersion
        ) {
          return;
        }
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
