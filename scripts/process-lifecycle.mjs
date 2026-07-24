import { connect } from "node:net";

export async function waitForCompletionOrTimeout(completion, timeoutMs) {
  let timeoutId;
  const timeout = new Promise((resolveTimeout) => {
    timeoutId = setTimeout(resolveTimeout, timeoutMs);
  });

  try {
    await Promise.race([completion, timeout]);
  } finally {
    clearTimeout(timeoutId);
  }
}

export function requestShutdownViaSocket(socketPath, timeoutMs = 500) {
  return new Promise((resolveRequest) => {
    let completed = false;
    const socket = connect(socketPath);
    const timeout = setTimeout(() => finish(false), timeoutMs);
    timeout.unref();

    function finish(accepted) {
      if (completed) return;
      completed = true;
      clearTimeout(timeout);
      socket.destroy();
      resolveRequest(accepted);
    }

    socket.once("connect", () => socket.write("quit\n"));
    socket.once("data", (data) => finish(data.toString("utf8").trim() === "ok"));
    socket.once("error", () => finish(false));
    socket.once("close", () => finish(false));
  });
}

export async function terminateWithFallback({
  isRunning,
  requestTermination,
  waitForExit,
  gracefulTimeoutMs,
  forceTimeoutMs,
  onForce
}) {
  if (!isRunning()) return false;

  requestTermination("SIGTERM");
  await waitForCompletionOrTimeout(waitForExit(), gracefulTimeoutMs);
  if (!isRunning()) return false;

  onForce();
  requestTermination("SIGKILL");
  await waitForCompletionOrTimeout(waitForExit(), forceTimeoutMs);
  return true;
}
