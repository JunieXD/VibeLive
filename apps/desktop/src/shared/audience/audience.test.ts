import { describe, expect, it } from 'vitest'
import {
  BASE_PERSONAS,
  BUILT_IN_MODES,
  activateMode,
  archiveMeme,
  archiveStaleMemes,
  autoIngestMeme,
  compileAudienceWorkspaceSnapshot,
  createInitialAudienceWorkspace,
  duplicateModeAsCustom,
  findMemeConflict,
  parseAudienceWorkspaceState,
  parsePersonaMarkdown,
  recordMemeUsage,
  resetBuiltInMode,
  restoreMeme,
  serializePersonaMarkdown,
  setMemePinned,
  undoAutomaticMeme,
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
    expect(BASE_PERSONAS.flatMap((persona) => validatePersona(persona))).toEqual([])

    const mode6657 = BUILT_IN_MODES.find((mode) => mode.id === 'room-6657')
    expect(mode6657).toMatchObject({
      ambience: 'continuous',
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
    const markdown = serializePersonaMarkdown(source)
    expect(markdown).toContain('"version": 1')
    const parsed = parsePersonaMarkdown(markdown)
    expect(parsed).toEqual({ ok: true, persona: source })
  })

  it('rejects prose or another fence before the JSON structure', () => {
    expect(parsePersonaMarkdown('# heading\n```json\n{}\n```')).toMatchObject({ ok: false })
    expect(parsePersonaMarkdown('```text\nx\n```\n```json\n{}\n```')).toMatchObject({ ok: false })
  })

  it('rejects unknown personality document versions', () => {
    const markdown = serializePersonaMarkdown(BASE_PERSONAS[0]).replace(
      '"version": 1',
      '"version": 2'
    )
    expect(parsePersonaMarkdown(markdown)).toMatchObject({
      ok: false,
      issues: [{ field: 'version' }]
    })
  })

  it('rejects missing, mistyped and unknown metadata fields instead of coercing them', () => {
    const valid = serializePersonaMarkdown(BASE_PERSONAS[0])
    expect(parsePersonaMarkdown(valid.replace('"enabled": true', '"enabled_typo": true'))).toMatchObject({
      ok: false,
      issues: expect.arrayContaining([expect.objectContaining({ field: 'enabled' })])
    })
    expect(parsePersonaMarkdown(valid.replace('"maxCommentsPerDecision": 2', '"maxCommentsPerDecision": "2"'))).toMatchObject({
      ok: false,
      issues: expect.arrayContaining([
        expect.objectContaining({ field: 'maxCommentsPerDecision' })
      ])
    })
    expect(parsePersonaMarkdown(valid.replace('"version": 1', '"version": 1,\n  "futureField": true'))).toMatchObject({
      ok: false,
      issues: expect.arrayContaining([expect.objectContaining({ field: 'futureField' })])
    })
  })
})

describe('meme lifecycle and runtime isolation', () => {
  const ingest = (modeId: string, id: string, text: string, familyKey?: string) =>
    autoIngestMeme([], {
      id,
      modeId,
      text,
      familyKey,
      personaTags: ['fun_seeker'],
      sourceKinds: ['user_speech'],
      evidenceSummary: '主播刚说出的短句',
      createdAt: '2026-07-23T00:00:00.000Z'
    })

  it('suppresses exact duplicates and same-family variants only inside a mode', () => {
    const first = ingest('room-6657', 'm1', '这波稳了', 'steady')
    expect(first.accepted).toBe(true)
    if (!first.accepted) return

    expect(autoIngestMeme(first.entries, {
      id: 'm2',
      modeId: 'room-6657',
      text: '这 波，稳了！',
      familyKey: 'other',
      sourceKinds: ['user_text'],
      evidenceSummary: '',
      createdAt: '2026-07-23T00:01:00.000Z'
    })).toMatchObject({ accepted: false, reason: 'duplicate' })
    expect(autoIngestMeme(first.entries, {
      id: 'm3',
      modeId: 'room-6657',
      text: '稳得不行',
      familyKey: 'steady',
      sourceKinds: ['screen_event'],
      evidenceSummary: '',
      createdAt: '2026-07-23T00:02:00.000Z'
    })).toMatchObject({ accepted: false, reason: 'family-suppressed' })
    expect(autoIngestMeme(first.entries, {
      id: 'm4',
      modeId: 'newcomer-friendly',
      text: '这波稳了',
      familyKey: 'steady',
      sourceKinds: ['manual'],
      evidenceSummary: '',
      createdAt: '2026-07-23T00:03:00.000Z'
    }).accepted).toBe(true)

    expect(findMemeConflict(first.entries, {
      id: 'manual-edit',
      modeId: 'room-6657',
      normalizedText: '这波稳了',
      familyKey: 'different'
    })).toBe('duplicate')
    expect(findMemeConflict(first.entries, {
      id: 'manual-edit',
      modeId: 'room-6657',
      normalizedText: '另一句话',
      familyKey: 'STEADY'
    })).toBe('family-suppressed')
  })

  it('archives, restores active, pins, records decay data and undoes automatic ingestion', () => {
    const result = ingest('room-6657', 'm1', '这波稳了')
    if (!result.accepted) throw new Error('fixture ingestion failed')
    let entries = archiveMeme(result.entries, 'm1')
    expect(entries[0]).toMatchObject({ status: 'archived', revision: 2 })
    entries = restoreMeme(entries, 'm1')
    entries = setMemePinned(entries, 'm1', true)
    entries = recordMemeUsage(entries, 'm1', '2026-07-23T00:04:00.000Z')
    expect(entries[0]).toMatchObject({
      status: 'active',
      pinned: true,
      revision: 5,
      usageCount: 1,
      lastUsedAt: '2026-07-23T00:04:00.000Z'
    })
    expect(undoAutomaticMeme(entries, 'm1')).toEqual([])
  })

  it('compiles active personas with overrides and memes from only the active mode', () => {
    const workspace = createInitialAudienceWorkspace()
    const active = activateMode(workspace.modeState, 'room-6657')
    const a = ingest('room-6657', 'm1', 'A')
    const b = ingest('newcomer-friendly', 'm2', 'B')
    if (!a.accepted || !b.accepted) throw new Error('fixture ingestion failed')
    const snapshot = compileAudienceWorkspaceSnapshot({
      ...workspace,
      modeState: active,
      memes: [...a.entries, ...b.entries]
    })
    expect(snapshot.mode.id).toBe('room-6657')
    expect(snapshot.mode).toMatchObject({
      baseActivity: [6, 10],
      burstLimit: [20, 28],
      ambience: 'continuous'
    })
    expect(snapshot.personas.map((persona) => persona.id)).toContain('reaction_qmark')
    expect(snapshot.personas.find((persona) => persona.id === 'reaction_qmark')?.weight).toBe(3)
    expect(snapshot.memes.map((meme) => meme.id)).toEqual(['m1'])
  })

  it('archives stale low-use memes while preserving pinned, frequent and invalid-date entries', () => {
    const fixture = ingest('room-6657', 'old', '旧梗')
    if (!fixture.accepted) throw new Error('fixture ingestion failed')
    const old = { ...fixture.entry, createdAt: '2026-01-01T00:00:00.000Z' }
    const entries = [
      old,
      { ...old, id: 'inactive', text: '停用梗', normalizedText: '停用梗', familyKey: 'inactive', status: 'inactive' as const },
      { ...old, id: 'pinned', text: '置顶梗', normalizedText: '置顶梗', familyKey: 'pinned', pinned: true },
      { ...old, id: 'frequent', text: '常用梗', normalizedText: '常用梗', familyKey: 'frequent', usageCount: 3 },
      { ...old, id: 'invalid', text: '坏日期', normalizedText: '坏日期', familyKey: 'invalid', createdAt: 'not-a-date' },
      { ...old, id: 'recent', text: '最近用过', normalizedText: '最近用过', familyKey: 'recent', lastUsedAt: '2026-02-20T00:00:00.000Z' }
    ]

    const archived = archiveStaleMemes(entries, '2026-03-01T00:00:00.000Z')
    expect(archived.find((entry) => entry.id === 'old')).toMatchObject({ status: 'archived', revision: 2 })
    expect(archived.find((entry) => entry.id === 'inactive')).toMatchObject({ status: 'archived', revision: 2 })
    expect(archived.find((entry) => entry.id === 'pinned')?.status).toBe('active')
    expect(archived.find((entry) => entry.id === 'frequent')?.status).toBe('active')
    expect(archived.find((entry) => entry.id === 'invalid')?.status).toBe('active')
    expect(archived.find((entry) => entry.id === 'recent')?.status).toBe('active')
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
      name: '自定义人格'
    })

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
  })

  it('rejects unsafe mode ids, damaged meme derivations and unknown overrides', () => {
    const unsafeMode = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    unsafeMode.modeState.modes[0].id = '../outside'
    unsafeMode.modeState.activeModeId = '../outside'
    expect(parseAudienceWorkspaceState(unsafeMode).ok).toBe(false)

    const damagedMeme = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    damagedMeme.memes.push({
      id: 'meme-damaged',
      modeId: damagedMeme.modeState.activeModeId,
      text: '同一个梗',
      normalizedText: '伪造值',
      familyKey: 'family',
      personaTags: [],
      sourceKinds: ['manual'],
      evidenceSummary: '',
      createdBy: 'user',
      source: 'manual',
      createdAt: '2026-07-23T00:00:00.000Z',
      revision: 1,
      lastUsedAt: null,
      usageCount: 0,
      status: 'active',
      pinned: false
    })
    expect(parseAudienceWorkspaceState(damagedMeme).ok).toBe(false)

    const unknownOverride = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    unknownOverride.modeState.modes[0].personaOverrides.missing_persona = { name: '不存在' }
    expect(parseAudienceWorkspaceState(unknownOverride).ok).toBe(false)
  })
})
