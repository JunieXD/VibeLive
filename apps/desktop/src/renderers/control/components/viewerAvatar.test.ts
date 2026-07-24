import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ViewerAvatar } from './ViewerAvatar'
import {
  leadingGrapheme,
  VIEWER_AVATAR_TONE_COUNT,
  VIEWER_AVATAR_VARIANT_COUNT,
  viewerAvatarTone,
  viewerAvatarVariant
} from './viewerAvatarUtils'

describe('viewer avatar helpers', () => {
  it('maps viewer seeds to stable bounded avatar variants and tones', () => {
    const variant = viewerAvatarVariant('avatar-1')
    const tone = viewerAvatarTone('color-1')

    expect(viewerAvatarVariant('avatar-1')).toBe(variant)
    expect(viewerAvatarTone('color-1')).toBe(tone)
    expect(variant).toBeGreaterThanOrEqual(0)
    expect(variant).toBeLessThan(VIEWER_AVATAR_VARIANT_COUNT)
    expect(tone).toBeGreaterThanOrEqual(0)
    expect(tone).toBeLessThan(VIEWER_AVATAR_TONE_COUNT)
  })

  it('keeps emoji and composed names intact when an initial is required', () => {
    expect(leadingGrapheme(' 观众甲')).toBe('观')
    expect(leadingGrapheme('👩‍💻开发者')).toBe('👩‍💻')
    expect(leadingGrapheme('')).toBe('?')
  })

  it('renders a seeded geometric avatar without leaking seed text into the UI', () => {
    const markup = renderToStaticMarkup(
      createElement(ViewerAvatar, {
        avatarSeed: 'avatar-1',
        colorSeed: 'color-1',
        className: 'viewer-avatar'
      })
    )

    expect(markup).toContain('class="viewer-avatar-visual viewer-avatar"')
    expect(markup).toContain('data-avatar-tone=')
    expect(markup).toContain('data-avatar-variant=')
    expect(markup).toContain('<svg')
    expect(markup).not.toContain('avatar-1')
    expect(markup).not.toContain('color-1')
  })
})
