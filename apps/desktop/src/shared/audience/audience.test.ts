import { describe, expect, it } from 'vitest'
import { createHash } from 'node:crypto'
import {
  BASE_PERSONAS,
  BUILT_IN_MODES,
  allocateViewerCounts,
  activateMode,
  compileViewerPool,
  canonicalPersonaContent,
  createInitialAudienceWorkspace,
  duplicateModeAsCustom,
  parseAudienceWorkspaceState,
  parsePersonaMarkdown,
  resetBuiltInMode,
  reviseAudienceMode,
  serializePersonaMarkdown,
  validatePersona
} from './index'

describe('audience presets and modes', () => {
  it('keeps the restored 32 stable personas and six built-in modes', () => {
    expect(BASE_PERSONAS).toHaveLength(32)
    expect(new Set(BASE_PERSONAS.map((persona) => persona.id)).size).toBe(32)
    expect(BASE_PERSONAS.map((persona) => persona.id)).toContain('reaction_qmark')
    expect(BASE_PERSONAS.map((persona) => persona.id)).toContain('instigator')
    expect(BASE_PERSONAS.find((persona) => persona.id === 'instigator')?.name).toBe('串子哥')
    expect(BUILT_IN_MODES).toHaveLength(6)
    expect(BUILT_IN_MODES.map((mode) => mode.targetConcurrentViewers))
      .toEqual([24, 28, 16, 14, 24, 14])
    expect(BASE_PERSONAS.flatMap((persona) => validatePersona(persona))).toEqual([])

    const mode6657 = BUILT_IN_MODES.find((mode) => mode.id === 'room-6657')
    expect(mode6657).toMatchObject({
      ambience: 'continuous',
      targetConcurrentViewers: 28,
      normalResponseRange: [6, 10],
      highlightResponseRange: [20, 28],
      baseActivity: [6, 10],
      burstLimit: [20, 28]
    })
    expect(mode6657?.personaWeights.reaction_qmark).toBe(3)
  })

  it('activates exactly one mode and can copy/reset without mutating built-ins', () => {
    const workspace = createInitialAudienceWorkspace()
    const activated = {
      ...activateMode(workspace.modeState, 'room-6657'),
      modes: workspace.modeState.modes.map((mode) =>
        mode.id === 'room-6657'
          ? {
              ...mode,
              personaOverrides: {
                reaction_qmark: {
                  traits: ['模式特征'],
                  triggerPreferences: ['模式事件']
                }
              }
            }
          : mode
      )
    }
    expect(activated.activeModeId).toBe('room-6657')

    const copied = duplicateModeAsCustom(activated, 'room-6657', 'my-room', '我的房间')
    expect(copied.activeModeId).toBe('my-room')
    expect(copied.modes.at(-1)?.builtIn).toBe(false)
    expect(copied.modes.at(-1)?.personaWeights).not.toBe(
      copied.modes.find((mode) => mode.id === 'room-6657')?.personaWeights
    )
    expect(copied.modes.at(-1)?.personaOverrides.reaction_qmark?.traits).not.toBe(
      copied.modes.find((mode) => mode.id === 'room-6657')
        ?.personaOverrides.reaction_qmark?.traits
    )
    expect(
      copied.modes.at(-1)?.personaOverrides.reaction_qmark?.triggerPreferences
    ).not.toBe(
      copied.modes.find((mode) => mode.id === 'room-6657')
        ?.personaOverrides.reaction_qmark?.triggerPreferences
    )

    const changed = {
      ...copied,
      modes: copied.modes.map((mode) =>
        mode.id === 'room-6657' ? { ...mode, name: '被改过' } : mode
      )
    }
    expect(resetBuiltInMode(changed, 'room-6657').modes.find(
      (mode) => mode.id === 'room-6657'
    )?.name).toBe('6657 玩机器风格')
  })
})

describe('persona Markdown', () => {
  it('round trips the first fenced JSON block and Markdown behavior', () => {
    const source = BASE_PERSONAS[0]
    expect(source.contentHash).toBe(
      `sha256:${createHash('sha256').update(canonicalPersonaContent(source)).digest('hex')}`
    )
    const markdown = serializePersonaMarkdown(source)
    expect(markdown).toContain('"document_version": 2')
    expect(markdown).toContain(`"content_hash": "${source.contentHash}"`)
    const parsed = parsePersonaMarkdown(markdown)
    expect(parsed).toEqual({ ok: true, persona: source })
  })

})

describe('viewer pool v2', () => {
  it('allocates exact viewers with deterministic Hamilton tie breaking', () => {
    const mode = {
      ...BUILT_IN_MODES[0],
      targetConcurrentViewers: 5,
      personaIds: ['reaction_qmark', 'cheat_suspector', 'praise_then_bite'],
      personaWeights: {
        reaction_qmark: 1,
        cheat_suspector: 1,
        praise_then_bite: 1
      }
    }
    expect(allocateViewerCounts(mode, BASE_PERSONAS)).toEqual([
      { personaId: 'reaction_qmark', count: 2 },
      { personaId: 'cheat_suspector', count: 2 },
      { personaId: 'praise_then_bite', count: 1 }
    ])

    const first = compileViewerPool(mode, BASE_PERSONAS, 'session-a')
    const second = compileViewerPool(mode, BASE_PERSONAS, 'session-a')
    expect(second).toEqual(first)
    expect(first).toHaveLength(5)
    expect(new Set(first.map((viewer) => viewer.viewerInstanceId)).size).toBe(5)
    expect(first[0]).toMatchObject({
      alias: '问号哥·01',
      ordinal: 1
    })
    expect(first[0].viewerInstanceId).toMatch(
      /^viewer:[0-9a-f]{8}:lively-game-room:reaction_qmark:01$/
    )
    expect(compileViewerPool(mode, BASE_PERSONAS, 'session-b')[0].viewerInstanceId)
      .not.toBe(first[0].viewerInstanceId)
    expect(compileViewerPool(mode, BASE_PERSONAS, 'session-b')[0].variant)
      .not.toEqual(first[0].variant)
  })

  it('keeps mode overrides isolated from base persona revision and hashes', () => {
    const base = BASE_PERSONAS[0]
    const mode = reviseAudienceMode(BUILT_IN_MODES[0], {
      personaOverrides: { [base.id]: { name: '模式问号' } }
    })
    const viewer = compileViewerPool(mode, BASE_PERSONAS)[0]
    expect(viewer.alias).toBe('模式问号·01')
    expect(viewer.personaRevision).toBe(base.revision)
    expect(viewer.personaContentHash).toBe(base.contentHash)
    expect(BASE_PERSONAS[0]).toBe(base)
    expect(reviseAudienceMode(mode, {
      personaOverrides: { [base.id]: { name: '模式问号' } }
    })).toBe(mode)
  })
})

describe('workspace persistence', () => {
  it('strictly hydrates a JSON round trip and rejects damaged references', () => {
    const jsonValue: unknown = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    expect(parseAudienceWorkspaceState(jsonValue).ok).toBe(true)

    const damaged = jsonValue as {
      modeState: { activeModeId: string }
    }
    damaged.modeState.activeModeId = 'missing-mode'
    const parsed = parseAudienceWorkspaceState(damaged)
    expect(parsed.ok).toBe(false)
    if (!parsed.ok) expect(parsed.issues).toContain('activeModeId must reference an existing mode')
  })

})
