import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

const FILE_NAME = "runtime-session.json";

export async function loadRuntimeSessionId(userDataPath: string): Promise<string | null> {
  try {
    const parsed = JSON.parse(
      await readFile(join(userDataPath, FILE_NAME), "utf8")
    ) as Record<string, unknown>;
    return typeof parsed.sessionId === "string" && parsed.sessionId.trim()
      ? parsed.sessionId.trim()
      : null;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    return null;
  }
}

export async function saveRuntimeSessionId(
  userDataPath: string,
  sessionId: string
): Promise<void> {
  const normalized = sessionId.trim();
  if (!normalized) throw new Error("runtime session ID 不能为空。");
  await mkdir(userDataPath, { recursive: true });
  const target = join(userDataPath, FILE_NAME);
  const temporary = `${target}.tmp`;
  await writeFile(temporary, JSON.stringify({ sessionId: normalized }, null, 2), "utf8");
  await rename(temporary, target);
}

export async function clearRuntimeSessionId(userDataPath: string): Promise<void> {
  await rm(join(userDataPath, FILE_NAME), { force: true });
}
