import { describe, expect, it } from 'vitest'
import { encodeBinaryEnvelope } from './realtime-binary'

describe('ADVX-BIN/1 encoder', () => {
  it('writes the fixed header, UTF-8 fields and body in network byte order', () => {
    const encoded = encodeBinaryEnvelope({
      mediaType: 'audio',
      sessionId: 'session-1',
      inputId: 'audio-1',
      capturedAtMs: 1_725_000_000_123,
      format: 'audio/pcm;rate=16000;channels=1;format=s16le',
      body: new Uint8Array([0x01, 0x02, 0xff])
    })
    const view = new DataView(encoded.buffer, encoded.byteOffset, encoded.byteLength)

    expect(new TextDecoder().decode(encoded.slice(0, 4))).toBe('ADVX')
    expect(view.getUint8(4)).toBe(1)
    expect(view.getUint8(5)).toBe(1)
    expect(view.getUint16(6)).toBe(9)
    expect(view.getUint16(8)).toBe(7)
    expect(view.getBigUint64(10)).toBe(1_725_000_000_123n)
    expect(view.getUint16(18)).toBe(44)
    expect(view.getUint32(20)).toBe(3)
    expect(new TextDecoder().decode(encoded.slice(24, 33))).toBe('session-1')
    expect(new TextDecoder().decode(encoded.slice(33, 40))).toBe('audio-1')
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
  })

  it('rejects invalid timestamps and empty media', () => {
    expect(() =>
      encodeBinaryEnvelope({
        mediaType: 'audio',
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
