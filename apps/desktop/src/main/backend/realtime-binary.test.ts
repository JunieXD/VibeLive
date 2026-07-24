import { describe, expect, it } from 'vitest'
import {
  encodeAtomicBinaryEnvelope,
  encodeBinaryEnvelope,
  formatImageMimeType
} from './realtime-binary'

describe('ADVX-BIN/3 encoder', () => {
  it('writes a compact JSON header with atomic audio turn metadata', () => {
    const encoded = encodeAtomicBinaryEnvelope({
      mediaType: 'audio',
      source: 'microphone',
      sessionId: 'session-1',
      inputId: 'audio-1',
      capturedAtMs: 123,
      format: 'audio/pcm',
      body: new Uint8Array([1, 2]),
      turnId: 'turn-1',
      systemAudioRequired: true
    })
    const view = new DataView(encoded.buffer, encoded.byteOffset, encoded.byteLength)
    const headerLength = view.getUint32(5)
    const header = JSON.parse(new TextDecoder().decode(encoded.slice(9, 9 + headerLength)))

    expect(new TextDecoder().decode(encoded.slice(0, 4))).toBe('ADVX')
    expect(view.getUint8(4)).toBe(3)
    expect(header).toEqual({
      media_type: 'audio',
      source: 'microphone',
      session_id: 'session-1',
      input_id: 'audio-1',
      captured_at_ms: 123,
      format: 'audio/pcm',
      body_length: 2,
      turn_id: 'turn-1',
      system_audio_required: true
    })
    expect([...encoded.slice(9 + headerLength)]).toEqual([1, 2])
  })

  it('rejects atomic audio without a turn id', () => {
    expect(() => encodeAtomicBinaryEnvelope({
      mediaType: 'audio',
      source: 'microphone',
      sessionId: 'session-1',
      inputId: 'audio-1',
      capturedAtMs: 123,
      format: 'audio/pcm',
      body: new Uint8Array([1])
    })).toThrow('turnId')
  })
})

describe('ADVX-BIN/2 encoder', () => {
  it('writes the fixed header, UTF-8 fields and body in network byte order', () => {
    const encoded = encodeBinaryEnvelope({
      mediaType: 'audio',
      source: 'system_audio',
      sessionId: 'session-1',
      inputId: 'audio-1',
      capturedAtMs: 1_725_000_000_123,
      format: 'audio/pcm;rate=16000;channels=1;format=s16le',
      body: new Uint8Array([0x01, 0x02, 0xff])
    })
    const view = new DataView(encoded.buffer, encoded.byteOffset, encoded.byteLength)

    expect(new TextDecoder().decode(encoded.slice(0, 4))).toBe('ADVX')
    expect(view.getUint8(4)).toBe(2)
    expect(view.getUint8(5)).toBe(1)
    expect(view.getUint8(6)).toBe(2)
    expect(view.getUint16(7)).toBe(9)
    expect(view.getUint16(9)).toBe(7)
    expect(view.getBigUint64(11)).toBe(1_725_000_000_123n)
    expect(view.getUint16(19)).toBe(44)
    expect(view.getUint32(21)).toBe(3)
    expect(new TextDecoder().decode(encoded.slice(25, 34))).toBe('session-1')
    expect(new TextDecoder().decode(encoded.slice(34, 41))).toBe('audio-1')
    expect([...encoded.slice(-3)]).toEqual([0x01, 0x02, 0xff])
  })

  it('uses media type 2 for image frames', () => {
    const encoded = encodeBinaryEnvelope({
      mediaType: 'image',
      sessionId: 's',
      inputId: 'i',
      capturedAtMs: 1,
      format: 'image/jpeg',
      body: new Uint8Array([1])
    })
    expect(encoded[5]).toBe(2)
    expect(encoded[6]).toBe(0)
  })

  it('serializes validated visual change metadata into the image format', () => {
    expect(formatImageMimeType('IMAGE/JPEG', 0.1256789))
      .toBe('image/jpeg;advx-change-score=0.125679')
    expect(() => formatImageMimeType('image/jpeg', Number.NaN)).toThrow('changeScore')
    expect(() => formatImageMimeType('image/jpeg', 1.01)).toThrow('changeScore')
  })

  it('rejects invalid timestamps and empty media', () => {
    expect(() =>
      encodeBinaryEnvelope({
        mediaType: 'audio',
        source: 'microphone',
        sessionId: 's',
        inputId: 'i',
        capturedAtMs: -1,
        format: 'audio/pcm',
        body: new Uint8Array([1])
      })
    ).toThrow('capturedAtMs')
    expect(() =>
      encodeBinaryEnvelope({
        mediaType: 'image',
        sessionId: 's',
        inputId: 'i',
        capturedAtMs: 1,
        format: 'image/jpeg',
        body: new Uint8Array()
      })
    ).toThrow('outside the allowed size')
  })
})
