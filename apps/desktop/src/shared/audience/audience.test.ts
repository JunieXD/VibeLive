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
  revisePersonaTemplate,
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
    expect(BUILT_IN_MODES.map((mode) => mode.viewerCount)).toEqual([24, 28, 16, 14, 24, 14])
    expect(BASE_PERSONAS.flatMap((persona) => validatePersona(persona))).toEqual([])

    const mode6657 = BUILT_IN_MODES.find((mode) => mode.id === 'room-6657')
    expect(mode6657).toMatchObject({
      ambience: 'continuous',
      viewerCount: 28,
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

  it('rejects prose or another fence before the JSON structure', () => {
    expect(parsePersonaMarkdown('# heading\n```json\n{}\n```')).toMatchObject({ ok: false })
    expect(parsePersonaMarkdown('```text\nx\n```\n```json\n{}\n```')).toMatchObject({ ok: false })
  })

  it('rejects unknown personality document versions', () => {
    const markdown = serializePersonaMarkdown(BASE_PERSONAS[0]).replace(
      '"document_version": 2',
      '"document_version": 3'
    )
    expect(parsePersonaMarkdown(markdown)).toMatchObject({
      ok: false,
      issues: [{ field: 'document_version' }]
    })
  })

  it('rejects missing, mistyped and unknown metadata fields instead of coercing them', () => {
    const valid = serializePersonaMarkdown(BASE_PERSONAS[0])
    expect(parsePersonaMarkdown(valid.replace('"enabled": true', '"enabled_typo": true'))).toMatchObject({
      ok: false,
      issues: expect.arrayContaining([expect.objectContaining({ field: 'enabled' })])
    })
    expect(parsePersonaMarkdown(valid.replace(
      '"max_comments_per_decision": 2',
      '"max_comments_per_decision": "2"'
    ))).toMatchObject({
      ok: false,
      issues: expect.arrayContaining([
        expect.objectContaining({ field: 'maxCommentsPerDecision' })
      ])
    })
    expect(parsePersonaMarkdown(valid.replace(
      '"document_version": 2',
      '"document_version": 2,\n  "futureField": true'
    ))).toMatchObject({
      ok: false,
      issues: expect.arrayContaining([expect.objectContaining({ field: 'futureField' })])
    })
  })

  it('uses canonical content hashes and increments revisions only for material changes', () => {
    const source = BASE_PERSONAS[0]
    expect(revisePersonaTemplate(source, {})).toBe(source)
    expect(revisePersonaTemplate(source, { behavior: `${source.behavior}\r\n` })).toBe(source)

    const revised = revisePersonaTemplate(source, { speechStyle: '更短' })
    expect(revised).toMatchObject({
      revision: source.revision + 1,
      speechStyle: '更短'
    })
    expect(revised.contentHash).not.toBe(source.contentHash)
    expect(parsePersonaMarkdown(serializePersonaMarkdown(revised))).toEqual({
      ok: true,
      persona: revised
    })
  })
})

describe('viewer pool v2', () => {
  it('allocates exact viewers with deterministic Hamilton tie breaking', () => {
    const mode = {
      ...BUILT_IN_MODES[0],
      viewerCount: 5,
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
  it('migrates v1 modes once and preserves the exact built-in viewer counts', () => {
    const v2 = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    const v1 = {
      ...v2,
      version: 1,
      personas: v2.personas.map((persona: Record<string, unknown>) => {
        const { documentVersion, revision, contentHash, ...legacy } = persona
        return legacy
      }),
      modeState: {
        ...v2.modeState,
        modes: v2.modeState.modes.map((mode: Record<string, unknown>) => {
          const {
            namespaceId,
            revision,
            viewerCount,
            normalResponseRange,
            highlightResponseRange,
            visualSettings,
            ...legacy
          } = mode
          return legacy
        })
      }
    }
    const parsed = parseAudienceWorkspaceState(v1)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.migratedFromVersion).toBe(1)
    expect(parsed.workspace.version).toBe(2)
    expect(parsed.workspace.modeState.modes.map((mode) => mode.viewerCount))
      .toEqual([24, 28, 16, 14, 24, 14])
    expect(parsed.workspace.modeState.modes[0].visualSettings).toMatchObject({
      viewerVisualInputMode: 'direct_frames',
      frameBundleSize: 3,
      frameSelectionStrategy: 'change_peaks'
    })
  })

  it('extracts v1 local memes for one-time Shared Brain migration', () => {
    const v1 = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    v1.version = 1
    v1.memes = [{
      id: 'legacy-joke',
      text: '这波属于是',
      createdAt: '2025-01-02T03:04:05.000Z'
    }]
    for (const persona of v1.personas) {
      delete persona.documentVersion
      delete persona.revision
      delete persona.contentHash
    }
    for (const mode of v1.modeState.modes) {
      delete mode.namespaceId
      delete mode.revision
      delete mode.viewerCount
      delete mode.normalResponseRange
      delete mode.highlightResponseRange
      delete mode.visualSettings
    }

    const parsed = parseAudienceWorkspaceState(v1)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.legacyMemes).toEqual([{
      id: 'legacy-joke',
      text: '这波属于是',
      createdAt: '2025-01-02T03:04:05.000Z'
    }])
    expect(parsed.workspace).not.toHaveProperty('memes')
  })

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

  it('keeps a valid mode-scoped override when that persona is not participating', () => {
    const workspace = createInitialAudienceWorkspace()
    const mode = workspace.modeState.modes[0]
    const inactivePersona = workspace.personas.find(
      (persona) => !mode.personaIds.includes(persona.id)
    )
    if (!inactivePersona) throw new Error('fixture needs a non-participating persona')
    const candidate = {
      ...workspace,
      modeState: {
        ...workspace.modeState,
        modes: workspace.modeState.modes.map((item) =>
          item.id === mode.id
            ? {
                ...item,
                personaOverrides: {
                  ...item.personaOverrides,
                  [inactivePersona.id]: { name: '待启用人格' }
                }
              }
            : item
        )
      }
    }
    expect(parseAudienceWorkspaceState(candidate).ok).toBe(true)
  })

  it('rehydrates the current built-in persona baseline without discarding custom data', () => {
    const candidate = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    candidate.personas = candidate.personas.filter(
      (persona: { id: string }) => persona.id !== BASE_PERSONAS[1].id
    )
    candidate.personas[0].name = '旧版本内置文案'
    candidate.personas.push({
      ...BASE_PERSONAS[0],
      id: 'custom-persona',
      name: '自定义人格',
      documentVersion: undefined,
      revision: undefined,
      contentHash: undefined
    })
    delete candidate.personas.at(-1).documentVersion
    delete candidate.personas.at(-1).revision
    delete candidate.personas.at(-1).contentHash

    const parsed = parseAudienceWorkspaceState(candidate)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.workspace.personas).toHaveLength(33)
    expect(parsed.workspace.personas.find((persona) => persona.id === BASE_PERSONAS[0].id)?.name)
      .toBe(BASE_PERSONAS[0].name)
    expect(parsed.workspace.personas.find((persona) => persona.id === BASE_PERSONAS[1].id))
      .toEqual(BASE_PERSONAS[1])
    expect(parsed.workspace.personas.find((persona) => persona.id === 'custom-persona')?.name)
      .toBe('自定义人格')
    expect(parsed.workspace.personas.find((persona) => persona.id === 'custom-persona'))
      .toMatchObject({ documentVersion: 2, revision: 1 })
  })

  it('returns validation issues instead of throwing on malformed nested values', () => {
    expect(() => parseAudienceWorkspaceState({
      version: 1,
      personas: [{}],
      modeState: { modes: [], activeModeId: '' },
      memes: []
    })).not.toThrow()
    expect(parseAudienceWorkspaceState({
      version: 1,
      personas: [{}],
      modeState: { modes: [], activeModeId: '' },
      memes: []
    }).ok).toBe(false)
    expect(parseAudienceWorkspaceState({ version: 3 }).ok).toBe(false)
  })

  it('rejects unsafe mode ids, legacy local memes and unknown overrides', () => {
    const unsafeMode = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    unsafeMode.modeState.modes[0].id = '../outside'
    unsafeMode.modeState.activeModeId = '../outside'
    expect(parseAudienceWorkspaceState(unsafeMode).ok).toBe(false)

    const legacyMeme = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    legacyMeme.memes = [{ id: 'legacy-local-meme' }]
    expect(parseAudienceWorkspaceState(legacyMeme)).toMatchObject({
      ok: false,
      issues: [expect.stringContaining('Shared Brain migration')]
    })

    const unknownOverride = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    unknownOverride.modeState.modes[0].personaOverrides.missing_persona = { name: '不存在' }
    expect(parseAudienceWorkspaceState(unknownOverride).ok).toBe(false)
  })
})
