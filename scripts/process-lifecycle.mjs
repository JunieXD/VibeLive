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
