import { describe, expect, it } from 'vitest'
import {
  concatenateFloat32,
  encodePcm16Mono,
  float32ToPcm16Le,
  resampleMono
} from './audio'

describe('desktop realtime audio encoding', () => {
  it('concatenates captured chunks in order', () => {
    expect([...concatenateFloat32([new Float32Array([1, 2]), new Float32Array([3])])]).toEqual([
      1, 2, 3
    ])
  })

  it('resamples mono audio to 16 kHz', () => {
    const input = new Float32Array(48_000).map((_, index) => index / 48_000)
    const output = resampleMono(input, 48_000)
    expect(output).toHaveLength(16_000)
    expect(output[8_000]).toBeCloseTo(0.5, 4)
  })

  it('encodes clipped signed PCM in little-endian order', () => {
    expect([...float32ToPcm16Le(new Float32Array([-2, -1, 0, 1, 2]))]).toEqual([
      0x00, 0x80, 0x00, 0x80, 0x00, 0x00, 0xff, 0x7f, 0xff, 0x7f
    ])
  })

  it('combines resampling and PCM encoding', () => {
    const encoded = encodePcm16Mono([new Float32Array(44_100)], 44_100)
    expect(encoded).toHaveLength(32_000)
  })
})
