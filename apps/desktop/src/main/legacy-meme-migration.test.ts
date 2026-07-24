import { describe, expect, it, vi } from "vitest";
import type { LegacyMemeMigrationClient } from "./legacy-meme-migration";
import {
  LegacyMemeMigrationError,
  migrateLegacyMemes,
  runLegacyMemeMigration
} from "./legacy-meme-migration";

const legacy = {
  id: "old-joke",
  text: "这波属于是",
  createdAt: "2025-01-02T03:04:05.000Z"
};
const scope = {
  roomId: "room-a",
  sessionId: "session-a",
  audienceEpoch: 1,
  namespaceId: "mode-a"
};

function client(
  overrides: Partial<LegacyMemeMigrationClient> = {}
): LegacyMemeMigrationClient {
  return {
    importLegacyMeme: vi.fn().mockResolvedValue({
      candidate_id: "legacy-candidate",
      meme_id: "legacy-meme",
      provenance_event_id: "legacy-provenance",
      created: true
    }),
    ...overrides
  };
}

describe("legacy meme migration", () => {
  it("imports legacy local memes into the active Mode namespace", async () => {
    const backend = client();
    await migrateLegacyMemes([legacy], scope, backend);

    expect(backend.importLegacyMeme).toHaveBeenCalledWith(
      "mode-a",
      expect.objectContaining({
        room_id: "room-a",
        session_id: "session-a",
        audience_epoch: 1,
        legacy_meme_id: "old-joke",
        text: legacy.text,
        legacy_created_at_ms: Date.parse(legacy.createdAt)
      })
    );
  });

  it("accepts the backend idempotent repeat result without a second local source", async () => {
    const backend = client({
      importLegacyMeme: vi.fn().mockResolvedValue({
        candidate_id: "stable-candidate",
        meme_id: "stable-meme",
        provenance_event_id: "stable-provenance",
        created: false
      })
    });

    await migrateLegacyMemes([legacy], scope, backend);
    expect(backend.importLegacyMeme).toHaveBeenCalledOnce();
  });

  it("keeps the migration pending when the dedicated import fails", async () => {
    const backend = client({
      importLegacyMeme: vi.fn().mockRejectedValue(new Error("legacy conflict"))
    });

    await expect(migrateLegacyMemes([legacy], scope, backend))
      .rejects.toThrow("legacy conflict");
  });
});

describe("legacy meme migration session orchestration", () => {
  it("stops the newly created session and does not persist the workspace on failure", async () => {
    const persistWorkspace = vi.fn();
    const stopSession = vi.fn().mockResolvedValue(undefined);
    const saveRecoverySession = vi.fn().mockResolvedValue(undefined);
    const clearRecoverySession = vi.fn().mockResolvedValue(undefined);

    await expect(runLegacyMemeMigration({
      sessionId: "session-a",
      migrate: vi.fn().mockRejectedValue(new Error("import failed")),
      persistWorkspace,
      saveRecoverySession,
      clearRecoverySession,
      stopSession
    })).rejects.toMatchObject({
      code: "legacy_meme_migration_failed",
      recoverySessionId: null
    });

    expect(persistWorkspace).not.toHaveBeenCalled();
    expect(saveRecoverySession).toHaveBeenCalledOnce();
    expect(stopSession).toHaveBeenCalledOnce();
    expect(clearRecoverySession).toHaveBeenCalledOnce();
  });

  it("returns a traceable recovery session when stopping the failed session also fails", async () => {
    await expect(runLegacyMemeMigration({
      sessionId: "session-a",
      migrate: vi.fn().mockRejectedValue(new Error("import failed")),
      persistWorkspace: vi.fn(),
      saveRecoverySession: vi.fn().mockResolvedValue(undefined),
      clearRecoverySession: vi.fn(),
      stopSession: vi.fn().mockRejectedValue(new Error("stop failed"))
    })).rejects.toEqual(expect.objectContaining({
      name: "LegacyMemeMigrationError",
      code: "legacy_meme_migration_cleanup_failed",
      recoverySessionId: "session-a",
      message: expect.stringContaining("session-a")
    }));
  });

  it("keeps failed work unsaved and allows an idempotent retry to finish", async () => {
    const persistWorkspace = vi.fn().mockResolvedValue(undefined);
    const stopSession = vi.fn().mockResolvedValue(undefined);
    const recovery = {
      saveRecoverySession: vi.fn().mockResolvedValue(undefined),
      clearRecoverySession: vi.fn().mockResolvedValue(undefined)
    };
    let attempts = 0;
    const migrate = vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("partial import");
    });

    await expect(runLegacyMemeMigration({
      sessionId: "session-a",
      migrate,
      persistWorkspace,
      stopSession,
      ...recovery
    })).rejects.toBeInstanceOf(LegacyMemeMigrationError);
    expect(persistWorkspace).not.toHaveBeenCalled();

    await runLegacyMemeMigration({
      sessionId: "session-b",
      migrate,
      persistWorkspace,
      stopSession,
      ...recovery
    });
    expect(migrate).toHaveBeenCalledTimes(2);
    expect(persistWorkspace).toHaveBeenCalledOnce();
  });
});
