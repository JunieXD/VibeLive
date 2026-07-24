import type { AudioSource, BackendTranscriptEvent } from '../../shared/contracts'

export type TranscriptRoute =
  | {
      kind: 'partial'
      source: AudioSource
      text: string
    }
  | {
      kind: 'final'
      source: AudioSource
      text: string
      author: '麦克风（主播）' | '系统声音'
      activityId: string
    }

export function routeBackendTranscript(event: BackendTranscriptEvent): TranscriptRoute {
  if (!event.final) {
    return {
      kind: 'partial',
      source: event.source,
      text: event.text
    }
  }
  return {
    kind: 'final',
    source: event.source,
    text: event.text,
    author: event.source === 'microphone' ? '麦克风（主播）' : '系统声音',
    activityId: event.utteranceId ?? `${event.startedAtMs}-${event.revision}`
  }
}
