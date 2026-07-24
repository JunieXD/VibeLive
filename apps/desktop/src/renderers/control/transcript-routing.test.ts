import { describe, expect, it } from 'vitest'
import type { BackendTranscriptEvent } from '../../shared/contracts'
import { routeBackendTranscript } from './transcript-routing'

function transcript(
  source: BackendTranscriptEvent['source'],
  final: boolean
): BackendTranscriptEvent {
  return {
    source,
    text: final ? '完成句子' : '正在识别',
    final,
    startedAtMs: 10,
    endedAtMs: 20,
    utteranceId: final ? 'utterance-1' : null,
    revision: 2
  }
}

describe('backend transcript routing', () => {
  it('keeps partial microphone text in status-only routing', () => {
    expect(routeBackendTranscript(transcript('microphone', false))).toEqual({
      kind: 'partial',
      source: 'microphone',
      text: '正在识别'
    })
  })

  it('labels final transcripts by their independent source', () => {
    expect(routeBackendTranscript(transcript('microphone', true))).toMatchObject({
      kind: 'final',
      author: '麦克风（主播）'
    })
    expect(routeBackendTranscript(transcript('system_audio', true))).toMatchObject({
      kind: 'final',
      author: '系统声音'
    })
  })
})
