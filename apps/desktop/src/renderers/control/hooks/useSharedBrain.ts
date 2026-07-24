import { useCallback, useEffect, useState } from 'react'
import type {
  AutoIngestResponse,
  MemeCandidate,
  ModeMeme,
  RoomLongTermMemory,
  RoomMemoryHead
} from '../../../shared/backend-client'

type UseSharedBrainOptions = {
  roomId: string | null
  namespaceId: string | null
  enabled: boolean
}

export function useSharedBrain({
  roomId,
  namespaceId,
  enabled
}: UseSharedBrainOptions) {
  const [memories, setMemories] = useState<readonly RoomLongTermMemory[]>([])
  const [memoryHead, setMemoryHead] = useState<RoomMemoryHead | null>(null)
  const [memes, setMemes] = useState<readonly ModeMeme[]>([])
  const [candidates, setCandidates] = useState<readonly MemeCandidate[]>([])
  const [autoIngest, setAutoIngest] = useState<AutoIngestResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [issue, setIssue] = useState<string | null>(null)

  const refresh = useCallback(async (): Promise<void> => {
    if (!enabled || !roomId || !namespaceId) {
      setMemories([])
      setMemoryHead(null)
      setMemes([])
      setCandidates([])
      setAutoIngest(null)
      return
    }
    setLoading(true)
    setIssue(null)
    try {
      const [nextMemories, nextMemoryHead, nextMemes, nextCandidates, nextAutoIngest] =
        await Promise.all([
          window.advx.listRoomMemories(roomId),
          window.advx.getRoomMemoryHead(roomId),
          window.advx.listModeMemes(namespaceId),
          window.advx.listPendingMemeCandidates(namespaceId),
          window.advx.getModeMemeAutoIngest(namespaceId)
        ])
      setMemories(nextMemories)
      setMemoryHead(nextMemoryHead)
      setMemes(nextMemes)
      setCandidates(nextCandidates)
      setAutoIngest(nextAutoIngest)
    } catch (error) {
      setIssue(describeError(error))
    } finally {
      setLoading(false)
    }
  }, [enabled, namespaceId, roomId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const mutate = useCallback(async (operation: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setIssue(null)
    try {
      await operation()
      await refresh()
    } catch (error) {
      setIssue(describeError(error))
    } finally {
      setBusy(false)
    }
  }, [refresh])

  return {
    memories,
    memoryHead,
    memes,
    candidates,
    autoIngest,
    loading,
    busy,
    issue,
    refresh,
    editMemory: (memory: RoomLongTermMemory, content: string) =>
      mutate(() => window.advx.editRoomMemory(memory.room_id, memory.memory_id, {
        content,
        expectedRevision: memory.revision
      })),
    revokeMemory: (memory: RoomLongTermMemory) =>
      mutate(() => window.advx.revokeRoomMemory(
        memory.room_id,
        memory.memory_id,
        memory.revision
      )),
    deleteMemory: (memory: RoomLongTermMemory) =>
      mutate(() => window.advx.deleteRoomMemory(
        memory.room_id,
        memory.memory_id,
        memory.revision
      )),
    resetMemories: () => roomId && memoryHead
      ? mutate(() => window.advx.resetRoomMemories(
          roomId,
          memoryResetRevision(memoryHead)
        ))
      : Promise.resolve(),
    setAutoIngest: (enabledValue: boolean) => namespaceId && autoIngest
      ? mutate(() => window.advx.setModeMemeAutoIngest(
          namespaceId,
          enabledValue,
          autoIngest.revision
        ))
      : Promise.resolve(),
    decideCandidate: (candidate: MemeCandidate, decision: 'approve' | 'reject') =>
      mutate(() => decision === 'approve'
        ? window.advx.approveMemeCandidate(candidate.namespace_id, candidate.candidate_id)
        : window.advx.rejectMemeCandidate(candidate.namespace_id, candidate.candidate_id)),
    mutateMeme: (
      meme: ModeMeme,
      action: Parameters<typeof window.advx.mutateModeMeme>[2]
    ) => mutate(() => window.advx.mutateModeMeme(
      meme.namespace_id,
      meme.meme_id,
      action,
      meme.revision
    )),
    editMeme: (meme: ModeMeme, text: string) =>
      mutate(() => window.advx.editModeMeme(meme.namespace_id, meme.meme_id, {
        text,
        expectedRevision: meme.revision
      }))
  }
}

export function memoryResetRevision(head: RoomMemoryHead): number {
  return head.revision
}

function describeError(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'Shared Brain 请求失败。'
}
