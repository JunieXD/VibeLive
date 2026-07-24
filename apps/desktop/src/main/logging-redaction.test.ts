import { describe, expect, it } from 'vitest'
import { redactLogData, redactLogText } from './logging-redaction'

describe('logging redaction', () => {
  it('removes secrets and binary payloads while preserving diagnostic context', () => {
    const token = 'local-token-value-123456'
    const apiKey = 'sk-test-secret-value'
    const [redacted] = redactLogData([
      {
        event: 'provider.request.failed',
        apiKey,
        headers: { Authorization: `Bearer ${token}` },
        nested: { localToken: token, status: 401 },
        payload: new Uint8Array([1, 2, 3])
      }
    ])
    const serialized = JSON.stringify(redacted)

    expect(serialized).not.toContain(token)
    expect(serialized).not.toContain(apiKey)
    expect(serialized).toContain('[REDACTED]')
    expect(serialized).toContain('[REDACTED_BINARY 3 bytes]')
    expect(serialized).toContain('provider.request.failed')
    expect(serialized).toContain('401')
  })

  it('redacts inline credentials without destroying ordinary error details', () => {
    const redacted = redactLogText(
      `request failed status=429 "api_key":"plain-provider-key" ` +
        `Authorization: Basic dXNlcjpwYXNz 'local_token': 'local-secret'`
    )

    expect(redacted).toContain('status=429')
    expect(redacted).not.toContain('plain-provider-key')
    expect(redacted).not.toContain('dXNlcjpwYXNz')
    expect(redacted).not.toContain('local-secret')
  })

  it('does not serialize custom object instances', () => {
    class ProviderClient {
      token = 'custom-object-secret'
    }

    expect(redactLogData([new ProviderClient()])).toEqual([
      '[REDACTED_OBJECT ProviderClient]'
    ])
  })
})
