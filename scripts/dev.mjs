import { spawn } from "node:child_process";

const useShell = process.platform === "win32";
const children = [
  spawn(
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
      "--port",
      "8765"
    ],
    { stdio: "inherit", shell: useShell }
  ),
  spawn("pnpm", ["--filter", "@advx/desktop", "dev"], {
    stdio: "inherit",
    shell: useShell
  })
];

let shuttingDown = false;

function shutdown(exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;

  for (const child of children) {
    if (!child.killed) child.kill();
  }

  process.exitCode = exitCode;
}

for (const child of children) {
  child.on("error", (error) => {
    console.error(error.message);
    shutdown(1);
  });

  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    if (signal) console.error(`Development process stopped by ${signal}.`);
    shutdown(code ?? 1);
  });
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
