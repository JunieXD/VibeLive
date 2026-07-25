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
  materializePersonaTemplate,
  parseAudienceWorkspaceState,
  parsePersonaMarkdown,
  resetBuiltInMode,
  reviseAudienceMode,
  serializePersonaMarkdown,
  totalViewerCount,
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
    expect(BUILT_IN_MODES.map(totalViewerCount))
      .toEqual([24, 28, 16, 14, 24, 14])
    expect(BUILT_IN_MODES.every(
      (mode) => mode.visualSettings.barrageGenerationMode === 'per_viewer'
    )).toBe(true)
    expect(BASE_PERSONAS.flatMap((persona) => validatePersona(persona))).toEqual([])

    const mode6657 = BUILT_IN_MODES.find((mode) => mode.id === 'room-6657')
    expect(mode6657).toMatchObject({
      ambience: 'continuous',
      revision: 2,
      normalResponseRange: [6, 10],
      highlightResponseRange: [20, 28],
      baseActivity: [6, 10],
      burstLimit: [20, 28]
    })
    expect(mode6657?.personaCounts.reaction_qmark).toBe(3)
    expect(mode6657?.personaOverrides.reaction_qmark?.speechStyle).toContain('1-8 字')
    expect(mode6657?.personaOverrides.meme_archivist?.avoidPatterns).toContain(
      '逐字复刻外部语料'
    )
    expect(Object.keys(mode6657?.personaOverrides ?? {})).toHaveLength(13)

    const reactionQmark = BASE_PERSONAS.find((persona) => persona.id === 'reaction_qmark')
    expect(reactionQmark).toBeDefined()
    const resolved = materializePersonaTemplate(
      reactionQmark!,
      mode6657?.personaOverrides.reaction_qmark
    )
    expect(resolved.speechStyle).toContain('1-8 字')
    expect(resolved.contentHash).not.toBe(reactionQmark?.contentHash)
    expect(validatePersona(resolved)).toEqual([])
    expect(() => serializePersonaMarkdown(resolved)).not.toThrow()

    const disabled = materializePersonaTemplate(resolved, { enabled: false })
    expect(disabled.contentHash).not.toBe(resolved.contentHash)
    expect(() => serializePersonaMarkdown(disabled)).not.toThrow()
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
    expect(copied.modes.at(-1)?.personaCounts).not.toBe(
      copied.modes.find((mode) => mode.id === 'room-6657')?.personaCounts
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
  it('allocates configured viewer counts exactly', () => {
    const mode = {
      ...BUILT_IN_MODES[0],
      personaCounts: {
        reaction_qmark: 2,
        cheat_suspector: 2,
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
            personaCounts,
            normalResponseRange,
            highlightResponseRange,
            visualSettings,
            ...legacy
          } = mode
          return {
            ...legacy,
            personaIds: Object.keys(personaCounts as Record<string, number>),
            personaWeights: { ...personaCounts as Record<string, number> },
            baseActivity: normalResponseRange,
            burstLimit: highlightResponseRange
          }
        })
      }
    }
    const parsed = parseAudienceWorkspaceState(v1)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.migratedFromVersion).toBe(1)
    expect(parsed.workspace.version).toBe(4)
    expect(parsed.workspace.modeState.modes.map(totalViewerCount))
      .toEqual([24, 28, 16, 14, 24, 14])
    expect(parsed.workspace.modeState.modes[0].visualSettings).toMatchObject({
      barrageGenerationMode: 'per_viewer',
      viewerVisualInputMode: 'direct_frames',
      frameBundleSize: 15,
      frameSelectionStrategy: 'change_peaks'
    })
  })

  it('migrates v2 and v3 weighted modes to their exact viewer counts', () => {
    for (const sourceVersion of [2, 3] as const) {
      const legacy = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
      legacy.version = sourceVersion
      for (const mode of legacy.modeState.modes) {
        const personaCounts = mode.personaCounts
        mode.personaIds = Object.keys(personaCounts)
        mode.personaWeights = { ...personaCounts }
        if (sourceVersion === 2) mode.viewerCount = totalViewerCount(mode)
        else mode.targetConcurrentViewers = totalViewerCount(mode)
        delete mode.personaCounts
      }

      const parsed = parseAudienceWorkspaceState(legacy)
      expect(parsed.ok).toBe(true)
      if (!parsed.ok) continue
      expect(parsed.migratedFromVersion).toBe(sourceVersion)
      expect(parsed.workspace.version).toBe(4)
      expect(parsed.workspace.modeState.modes.map(totalViewerCount))
        .toEqual([24, 28, 16, 14, 24, 14])
    }
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
      const personaCounts = mode.personaCounts
      mode.personaIds = Object.keys(personaCounts)
      mode.personaWeights = { ...personaCounts }
      mode.baseActivity = [...mode.normalResponseRange]
      mode.burstLimit = [...mode.highlightResponseRange]
      delete mode.personaCounts
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

  it('upgrades the former short visual default in an existing workspace', () => {
    const workspace = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    workspace.modeState.modes[0].visualSettings = {
      ...workspace.modeState.modes[0].visualSettings,
      frameBundleSize: 3,
      frameWindowMs: 10_000,
      frameSelectionStrategy: 'change_peaks'
    }

    const parsed = parseAudienceWorkspaceState(workspace)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.workspace.modeState.modes[0].visualSettings).toMatchObject({
      frameBundleSize: 15,
      frameWindowMs: 120_000
    })
  })

  it('defaults old visual settings to per-viewer generation without rejecting them', () => {
    const workspace = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    delete workspace.modeState.modes[0].visualSettings.barrageGenerationMode

    const parsed = parseAudienceWorkspaceState(workspace)

    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.workspace.modeState.modes[0].visualSettings.barrageGenerationMode)
      .toBe('per_viewer')
  })

  it('normalizes persisted window generation settings to backend-compatible limits', () => {
    const workspace = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    workspace.modeState.modes[0].visualSettings = {
      ...workspace.modeState.modes[0].visualSettings,
      barrageGenerationMode: 'window_batch',
      viewerVisualInputMode: 'shared_summary',
      frameBundleSize: 2,
      frameWindowMs: 10_000,
      frameSelectionStrategy: 'latest_n'
    }

    const parsed = parseAudienceWorkspaceState(workspace)

    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.workspace.modeState.modes[0].visualSettings).toMatchObject({
      barrageGenerationMode: 'window_batch',
      viewerVisualInputMode: 'direct_frames',
      frameBundleSize: 5,
      frameWindowMs: 30_000,
      frameSelectionStrategy: 'change_peaks'
    })
  })

  it('upgrades untouched built-in modes while preserving edited revisions', () => {
    const workspace = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    const stored6657 = workspace.modeState.modes.find(
      (mode: { id: string }) => mode.id === 'room-6657'
    )
    stored6657.revision = 1
    stored6657.personaOverrides = {}

    const upgraded = parseAudienceWorkspaceState(workspace)
    expect(upgraded.ok).toBe(true)
    if (!upgraded.ok) return
    const upgraded6657 = upgraded.workspace.modeState.modes.find(
      (mode) => mode.id === 'room-6657'
    )
    expect(upgraded6657?.revision).toBe(2)
    expect(Object.keys(upgraded6657?.personaOverrides ?? {})).toHaveLength(13)

    stored6657.revision = 2
    stored6657.description = '用户修改过的模式'
    const preserved = parseAudienceWorkspaceState(workspace)
    expect(preserved.ok).toBe(true)
    if (!preserved.ok) return
    expect(preserved.workspace.modeState.modes.find(
      (mode) => mode.id === 'room-6657'
    )?.description).toBe('用户修改过的模式')
  })

  it('caps former frame bundle settings at fifteen', () => {
    const workspace = JSON.parse(JSON.stringify(createInitialAudienceWorkspace()))
    workspace.modeState.modes[0].visualSettings = {
      ...workspace.modeState.modes[0].visualSettings,
      frameBundleSize: 60
    }

    const parsed = parseAudienceWorkspaceState(workspace)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.workspace.modeState.modes[0].visualSettings.frameBundleSize).toBe(15)
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

})
