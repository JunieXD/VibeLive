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
