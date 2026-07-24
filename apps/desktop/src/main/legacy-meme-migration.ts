import type {
  LegacyMemeImportRequest,
  LegacyMemeImportResponse
} from "../shared/backend-client";
import type { LegacyLocalMeme } from "../shared/audience";

export type LegacyMemeMigrationScope = {
  roomId: string;
  sessionId: string;
  audienceEpoch: number;
  namespaceId: string;
};

export type LegacyMemeMigrationClient = {
  importLegacyMeme(
    namespaceId: string,
    legacy: LegacyMemeImportRequest
  ): Promise<LegacyMemeImportResponse>;
};

export class LegacyMemeMigrationError extends Error {
  readonly code:
    | "legacy_meme_migration_failed"
    | "legacy_meme_migration_cleanup_failed";
  readonly recoverySessionId: string | null;

  constructor(
    code: LegacyMemeMigrationError["code"],
    message: string,
    recoverySessionId: string | null
  ) {
    super(`[${code}] ${message}`);
    this.name = "LegacyMemeMigrationError";
    this.code = code;
    this.recoverySessionId = recoverySessionId;
  }
}

export async function runLegacyMemeMigration(input: {
  sessionId: string;
  migrate: () => Promise<void>;
  persistWorkspace: () => Promise<void>;
  saveRecoverySession: () => Promise<void>;
  clearRecoverySession: () => Promise<void>;
  stopSession: () => Promise<unknown>;
}): Promise<void> {
  try {
    await input.migrate();
    await input.persistWorkspace();
  } catch (error) {
    await stopSessionAfterMigrationFailure(input, error);
  }
}

async function stopSessionAfterMigrationFailure(
  input: {
    sessionId: string;
    saveRecoverySession: () => Promise<void>;
    clearRecoverySession: () => Promise<void>;
    stopSession: () => Promise<unknown>;
  },
  migrationError: unknown
): Promise<never> {
  const migrationReason = errorMessage(migrationError);
  let recoverySaved = false;
  let recoverySaveError: unknown = null;
  try {
    await input.saveRecoverySession();
    recoverySaved = true;
  } catch (error) {
    recoverySaveError = error;
  }

  try {
    await input.stopSession();
  } catch (stopError) {
    if (!recoverySaved) {
      try {
        await input.saveRecoverySession();
        recoverySaved = true;
      } catch (retryError) {
        recoverySaveError = retryError;
      }
    }
    const recoveryDetail = recoverySaved
      ? `Recovery session ID 已记录：${input.sessionId}。`
      : `Recovery session ID：${input.sessionId}；持久化失败：${errorMessage(recoverySaveError)}。`;
    throw new LegacyMemeMigrationError(
      "legacy_meme_migration_cleanup_failed",
      `${migrationReason} 新建 Session 停止失败：${errorMessage(stopError)}。${recoveryDetail}`,
      input.sessionId
    );
  }

  if (recoverySaved) {
    try {
      await input.clearRecoverySession();
    } catch (clearError) {
      throw new LegacyMemeMigrationError(
        "legacy_meme_migration_cleanup_failed",
        `${migrationReason} Session 已停止，但 recovery 记录清理失败：${errorMessage(clearError)}。Recovery session ID：${input.sessionId}。`,
        input.sessionId
      );
    }
  }
  throw new LegacyMemeMigrationError(
    "legacy_meme_migration_failed",
    `${migrationReason} 新建 Session 已停止，旧版配置未覆盖。`,
    null
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "未知错误";
}

export async function migrateLegacyMemes(
  memes: readonly LegacyLocalMeme[],
  scope: LegacyMemeMigrationScope,
  client: LegacyMemeMigrationClient
): Promise<void> {
  for (const legacy of memes) {
    await client.importLegacyMeme(scope.namespaceId, {
      room_id: scope.roomId,
      session_id: scope.sessionId,
      audience_epoch: scope.audienceEpoch,
      legacy_meme_id: legacy.id,
      text: legacy.text,
      legacy_created_at_ms: legacy.createdAt ? Date.parse(legacy.createdAt) : null
    });
  }
}
