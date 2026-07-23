import { Library, RefreshCw, RotateCcw, ShieldAlert, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  BASE_PERSONAS,
  activateMode,
  duplicateModeAsCustom,
  findMemeConflict,
  normalizeMemeText,
  parsePersonaMarkdown,
  resetBuiltInMode,
  restoreMeme,
  serializePersonaMarkdown,
  validatePersona,
  type AudienceMode,
  type AudienceWorkspaceState,
  type MemeEntry,
  type MemeStatus,
  type Persona,
  type PersonaOverride
} from '../../../../shared/audience'
import type { SessionStatus } from '../../../../shared/session'
import { MemeEditor } from './MemeEditor'
import { MemeList } from './MemeList'
import { ModeToolbar } from './ModeToolbar'
import { PersonaEditor } from './PersonaEditor'
import { PersonaList } from './PersonaList'
import { cx } from './styles'

export type AudienceWorkspaceProps = {
  workspace: AudienceWorkspaceState
  sessionStatus: SessionStatus
  persistenceReady: boolean
  persistenceIssue: string | null
  onChange(next: AudienceWorkspaceState): void
  onRetryLoad(): void
  onResetRejected(): void
}

type WorkspaceTab = 'personas' | 'memes'
type EditorTab = 'form' | 'markdown'
type MemeFilter = 'all' | MemeStatus | 'new'

type MemeDraft = {
  text: string
  familyKey: string
  personaTags: string
  evidenceSummary: string
}

const BUILT_IN_PERSONA_IDS = new Set(BASE_PERSONAS.map((persona) => persona.id))
const STRUCTURE_LOCKED_STATUSES = new Set<SessionStatus>(['starting', 'running', 'stopping'])
const MEME_SOURCE_LABELS: Record<MemeEntry['sourceKinds'][number], string> = {
  user_text: '用户文字',
  user_speech: '用户语音',
  screen_event: '画面事件',
  audience_barrage: 'AI 互动',
  manual: '手动'
}
const MEME_STATUS_LABELS: Record<MemeStatus, string> = {
  active: '启用',
  inactive: '停用',
  archived: '归档'
}

function createStableId(prefix: string): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replaceAll('-', '').slice(0, 12)
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`
  return `${prefix}-${random}`
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function effectivePersona(base: Persona, mode: AudienceMode): Persona {
  return { ...base, ...mode.personaOverrides[base.id], id: base.id }
}

function personaOverride(base: Persona, next: Persona): PersonaOverride {
  const override: Record<string, unknown> = {}
  for (const key of [
    'name',
    'initials',
    'role',
    'color',
    'traits',
    'speechStyle',
    'behavior',
    'triggerPreferences',
    'avoidPatterns',
    'silenceBias',
    'burstBias',
    'repetitionBias',
    'cooldownMs',
    'maxCommentsPerDecision',
    'contentFlags',
    'enabled'
  ] as const) {
    const baseValue = base[key]
    const nextValue = next[key]
    if (Array.isArray(baseValue) && Array.isArray(nextValue)) {
      if (JSON.stringify(baseValue) !== JSON.stringify(nextValue)) override[key] = [...nextValue]
    } else if (baseValue !== nextValue) {
      override[key] = nextValue
    }
  }
  return override as PersonaOverride
}

function updateActiveMode(
  workspace: AudienceWorkspaceState,
  update: (mode: AudienceMode) => AudienceMode
): AudienceWorkspaceState {
  return {
    ...workspace,
    modeState: {
      ...workspace.modeState,
      modes: workspace.modeState.modes.map((mode) =>
        mode.id === workspace.modeState.activeModeId ? update(mode) : mode
      )
    }
  }
}

function updateMeme(
  entries: readonly MemeEntry[],
  id: string,
  change: Partial<MemeEntry>
): readonly MemeEntry[] {
  return entries.map((entry) =>
    entry.id === id ? { ...entry, ...change, revision: entry.revision + 1 } : entry
  )
}

export function AudienceWorkspace({
  workspace,
  sessionStatus,
  persistenceReady,
  persistenceIssue,
  onChange,
  onRetryLoad,
  onResetRejected
}: AudienceWorkspaceProps): React.JSX.Element {
  const [tab, setTab] = useState<WorkspaceTab>('personas')
  const [editorTab, setEditorTab] = useState<EditorTab>('form')
  const [personaSearch, setPersonaSearch] = useState('')
  const [selectedPersonaId, setSelectedPersonaId] = useState(
    workspace.personas[0]?.id ?? ''
  )
  const [personaDraft, setPersonaDraft] = useState<Persona | null>(null)
  const [markdownDraft, setMarkdownDraft] = useState('')
  const [personaError, setPersonaError] = useState('')
  const [modeNameDraft, setModeNameDraft] = useState('')
  const [memeSearch, setMemeSearch] = useState('')
  const [memeFilter, setMemeFilter] = useState<MemeFilter>('all')
  const [selectedMemeId, setSelectedMemeId] = useState('')
  const [memeDraft, setMemeDraft] = useState<MemeDraft | null>(null)
  const [memeError, setMemeError] = useState('')

  const structureLocked = STRUCTURE_LOCKED_STATUSES.has(sessionStatus)
  const activeMode =
    workspace.modeState.modes.find((mode) => mode.id === workspace.modeState.activeModeId) ??
    workspace.modeState.modes[0]
  const selectedBasePersona =
    workspace.personas.find((persona) => persona.id === selectedPersonaId) ??
    workspace.personas[0]
  const currentPersona = selectedBasePersona && activeMode
    ? effectivePersona(selectedBasePersona, activeMode)
    : null

  useEffect(() => {
    setModeNameDraft(activeMode?.name ?? '')
  }, [activeMode?.id, activeMode?.name])

  useEffect(() => {
    if (!currentPersona) {
      setPersonaDraft(null)
      setMarkdownDraft('')
      return
    }
    setPersonaDraft(currentPersona)
    setMarkdownDraft(serializePersonaMarkdown(currentPersona))
    setPersonaError('')
  }, [activeMode?.id, selectedBasePersona])

  const modeMemes = useMemo(
    () => workspace.memes.filter((entry) => entry.modeId === activeMode?.id),
    [activeMode?.id, workspace.memes]
  )
  const visibleMemes = useMemo(() => {
    const query = memeSearch.trim().toLocaleLowerCase()
    return modeMemes.filter((entry) => {
      const matchesSearch =
        !query ||
        `${entry.text} ${entry.familyKey} ${entry.personaTags.join(' ')} ${entry.evidenceSummary}`
          .toLocaleLowerCase()
          .includes(query)
      const matchesStatus =
        memeFilter === 'all' ||
        (memeFilter === 'new'
          ? entry.status === 'active' && entry.usageCount === 0
          : entry.status === memeFilter)
      return matchesSearch && matchesStatus
    })
  }, [memeFilter, memeSearch, modeMemes])
  const selectedMeme =
    modeMemes.find((entry) => entry.id === selectedMemeId) ?? visibleMemes[0]

  useEffect(() => {
    if (!selectedMeme) {
      setMemeDraft(null)
      setMemeError('')
      return
    }
    setMemeDraft({
      text: selectedMeme.text,
      familyKey: selectedMeme.familyKey,
      personaTags: selectedMeme.personaTags.join(', '),
      evidenceSummary: selectedMeme.evidenceSummary
    })
    setMemeError('')
  }, [selectedMeme?.id])

  if (!activeMode) {
    return (
      <div className={cx('audience-workspace', 'aw-empty')} data-audience-workspace>
        没有可用的观众模式
      </div>
    )
  }

  const emitMode = (nextMode: AudienceMode): void => {
    onChange(updateActiveMode(workspace, () => nextMode))
  }

  const patchMode = (change: Partial<AudienceMode>): void => {
    onChange(updateActiveMode(workspace, (mode) => ({ ...mode, ...change })))
  }

  const commitModeName = (): void => {
    const name = modeNameDraft.trim()
    if (!name) {
      setModeNameDraft(activeMode.name)
      return
    }
    if (name !== activeMode.name) patchMode({ name })
  }

  const setPersonaParticipation = (personaId: string, enabled: boolean): void => {
    const base = workspace.personas.find((persona) => persona.id === personaId)
    if (!base) return
    const resolved = effectivePersona(base, activeMode)
    const personaIds =
      enabled && !activeMode.personaIds.includes(personaId)
        ? [...activeMode.personaIds, personaId]
        : activeMode.personaIds
    const personaWeights = { ...activeMode.personaWeights }
    if (enabled && !Object.hasOwn(personaWeights, personaId)) personaWeights[personaId] = 1
    const nextPersona = { ...resolved, enabled }
    const override = personaOverride(base, nextPersona)
    const personaOverrides = { ...activeMode.personaOverrides }
    if (Object.keys(override).length === 0) delete personaOverrides[personaId]
    else personaOverrides[personaId] = override
    patchMode({ personaIds, personaWeights, personaOverrides })
    if (personaDraft?.id === personaId) {
      setPersonaDraft({ ...personaDraft, enabled })
      const parsedMarkdown = parsePersonaMarkdown(markdownDraft)
      if (parsedMarkdown.ok && parsedMarkdown.persona.id === personaId) {
        setMarkdownDraft(
          serializePersonaMarkdown({ ...parsedMarkdown.persona, enabled })
        )
      }
    }
  }

  const setPersonaWeight = (personaId: string, weight: number): void => {
    patchMode({
      personaWeights: { ...activeMode.personaWeights, [personaId]: weight }
    })
  }

  const choosePersona = (personaId: string): void => {
    setSelectedPersonaId(personaId)
    setEditorTab('form')
  }

  const persistPersonaOverride = (base: Persona, next: Persona): void => {
    const override = personaOverride(base, next)
    const personaOverrides = { ...activeMode.personaOverrides }
    if (Object.keys(override).length === 0) delete personaOverrides[next.id]
    else personaOverrides[next.id] = override
    emitMode({ ...activeMode, personaOverrides })
  }

  const savePersona = (): void => {
    if (!personaDraft || !selectedBasePersona) return
    const issues = validatePersona(personaDraft)
    if (issues.length > 0) {
      setPersonaError(issues.map((issue) => issue.message).join('；'))
      return
    }
    persistPersonaOverride(selectedBasePersona, personaDraft)
    setMarkdownDraft(serializePersonaMarkdown(personaDraft))
    setPersonaError('')
  }

  const resetPersona = (): void => {
    if (!selectedBasePersona) return
    const personaOverrides = { ...activeMode.personaOverrides }
    delete personaOverrides[selectedBasePersona.id]
    emitMode({ ...activeMode, personaOverrides })
    setPersonaDraft(selectedBasePersona)
    setMarkdownDraft(serializePersonaMarkdown(selectedBasePersona))
    setPersonaError('')
  }

  const applyMarkdown = (): void => {
    if (!selectedBasePersona) return
    const result = parsePersonaMarkdown(markdownDraft)
    if (!result.ok) {
      setPersonaError(result.issues.map((issue) => issue.message).join('；'))
      return
    }
    if (result.persona.id !== selectedPersonaId) {
      setPersonaError('人格 id 不可在高级编辑器中修改')
      return
    }
    setPersonaDraft(result.persona)
    persistPersonaOverride(selectedBasePersona, result.persona)
    setPersonaError('')
  }

  const addCustomPersona = (): void => {
    if (structureLocked) return
    const id = createStableId('custom')
    const persona: Persona = {
      id,
      name: '自定义观众',
      initials: '新',
      role: '待配置角色',
      color: '#6f7c91',
      traits: ['自定义'],
      speechStyle: '简短自然',
      behavior: '仅在符合触发偏好且不会打断直播节奏时发言。',
      triggerPreferences: [],
      avoidPatterns: [],
      silenceBias: 2,
      burstBias: 2,
      repetitionBias: 1,
      cooldownMs: 10_000,
      maxCommentsPerDecision: 1,
      contentFlags: [],
      enabled: true
    }
    onChange({
      ...updateActiveMode(workspace, (mode) => ({
        ...mode,
        personaIds: [...mode.personaIds, id],
        personaWeights: { ...mode.personaWeights, [id]: 1 }
      })),
      personas: [...workspace.personas, persona]
    })
    setSelectedPersonaId(id)
  }

  const deleteCustomPersona = (): void => {
    if (
      structureLocked ||
      !selectedBasePersona ||
      BUILT_IN_PERSONA_IDS.has(selectedBasePersona.id)
    ) {
      return
    }
    const personaId = selectedBasePersona.id
    const modes = workspace.modeState.modes.map((mode) => {
      const personaWeights = { ...mode.personaWeights }
      const personaOverrides = { ...mode.personaOverrides }
      delete personaWeights[personaId]
      delete personaOverrides[personaId]
      return {
        ...mode,
        personaIds: mode.personaIds.filter((id) => id !== personaId),
        personaWeights,
        personaOverrides
      }
    })
    onChange({
      ...workspace,
      personas: workspace.personas.filter((persona) => persona.id !== personaId),
      modeState: { ...workspace.modeState, modes }
    })
    setSelectedPersonaId(workspace.personas.find((persona) => persona.id !== personaId)?.id ?? '')
  }

  const duplicateMode = (): void => {
    if (structureLocked) return
    const id = createStableId(activeMode.id.replace(/[^a-z0-9-]/g, '') || 'mode')
    onChange({
      ...workspace,
      modeState: duplicateModeAsCustom(
        workspace.modeState,
        activeMode.id,
        id,
        `${activeMode.name} 副本`
      )
    })
  }

  const deleteMode = (): void => {
    if (structureLocked || activeMode.builtIn) return
    const modes = workspace.modeState.modes.filter((mode) => mode.id !== activeMode.id)
    onChange({
      ...workspace,
      modeState: {
        modes,
        activeModeId: modes[0]?.id ?? ''
      },
      memes: workspace.memes.filter((entry) => entry.modeId !== activeMode.id)
    })
  }

  const resetActiveBuiltInMode = (): void => {
    if (!activeMode.builtIn || structureLocked) return
    onChange({
      ...workspace,
      modeState: resetBuiltInMode(workspace.modeState, activeMode.id)
    })
    if (selectedBasePersona) {
      setPersonaDraft(selectedBasePersona)
      setMarkdownDraft(serializePersonaMarkdown(selectedBasePersona))
      setPersonaError('')
    }
  }

  const mutateMemes = (memes: readonly MemeEntry[]): void => {
    onChange({ ...workspace, memes })
  }

  const addManualMeme = (): void => {
    const now = new Date().toISOString()
    const id = createStableId('meme')
    let ordinal = 1
    let text = `新梗 ${ordinal}`
    while (
      modeMemes.some(
        (entry) =>
          entry.status !== 'archived' &&
          entry.normalizedText === normalizeMemeText(text)
      )
    ) {
      ordinal += 1
      text = `新梗 ${ordinal}`
    }
    const entry: MemeEntry = {
      id,
      modeId: activeMode.id,
      text,
      normalizedText: normalizeMemeText(text),
      familyKey: id,
      personaTags: [],
      sourceKinds: ['manual'],
      evidenceSummary: '',
      createdBy: 'user',
      source: 'manual',
      createdAt: now,
      revision: 1,
      lastUsedAt: null,
      usageCount: 0,
      status: 'active',
      pinned: false
    }
    mutateMemes([...workspace.memes, entry])
    setSelectedMemeId(id)
  }

  const saveMeme = (): void => {
    if (!selectedMeme || !memeDraft) return
    const text = memeDraft.text.trim()
    if (!text) {
      setMemeError('弹幕文本不能为空')
      return
    }
    const normalizedText = normalizeMemeText(text)
    const familyKey = (
      memeDraft.familyKey.trim() || normalizedText
    ).toLocaleLowerCase()
    const conflict = findMemeConflict(workspace.memes, {
      id: selectedMeme.id,
      modeId: selectedMeme.modeId,
      normalizedText,
      familyKey
    })
    if (conflict) {
      setMemeError(
        conflict === 'duplicate'
          ? '当前模式已有相同弹幕'
          : '当前模式已有同一梗家族的条目'
      )
      return
    }
    mutateMemes(
      updateMeme(workspace.memes, selectedMeme.id, {
        text,
        normalizedText,
        familyKey,
        personaTags: splitList(memeDraft.personaTags),
        evidenceSummary: memeDraft.evidenceSummary.trim().slice(0, 160)
      })
    )
    setMemeError('')
  }

  const restoreSelectedMeme = (): void => {
    if (!selectedMeme) return
    try {
      mutateMemes(restoreMeme(workspace.memes, selectedMeme.id))
      setMemeError('')
    } catch {
      setMemeError('当前模式已有相同文本或同一梗家族，无法恢复')
    }
  }

  const personaRows = workspace.personas.filter((persona) => {
    const query = personaSearch.trim().toLocaleLowerCase()
    return (
      !query ||
      `${persona.name} ${persona.initials} ${persona.role} ${persona.traits.join(' ')}`
        .toLocaleLowerCase()
        .includes(query)
    )
  })
  const stats = {
    active: modeMemes.filter((entry) => entry.status === 'active').length,
    new: modeMemes.filter((entry) => entry.status === 'active' && entry.usageCount === 0).length,
    archived: modeMemes.filter((entry) => entry.status === 'archived').length
  }

  return (
    <section
      className={cx('audience-workspace', !persistenceReady && 'has-persistence-alert')}
      data-audience-workspace
    >
      {!persistenceReady && (
        <div
          className={cx('aw-persistence-alert')}
          role={persistenceIssue ? 'alert' : 'status'}
        >
          <ShieldAlert size={18} />
          <div>
            <strong>{persistenceIssue ? '本地配置已保护' : '正在加载本地配置'}</strong>
            <span>
              {persistenceIssue ?? '载入完成前暂不允许编辑或保存。'}
            </span>
          </div>
          {persistenceIssue && (
            <div className={cx('aw-persistence-actions')}>
              <button type="button" onClick={onRetryLoad}>
                <RefreshCw size={14} />
                重试加载
              </button>
              <button type="button" className={cx('danger')} onClick={onResetRejected}>
                <RotateCcw size={14} />
                重置默认
              </button>
            </div>
          )}
        </div>
      )}
      <ModeToolbar
        workspace={workspace}
        activeMode={activeMode}
        structureLocked={structureLocked}
        modeNameDraft={modeNameDraft}
        onModeNameDraftChange={setModeNameDraft}
        onModeNameCommit={commitModeName}
        onSelectMode={(modeId) =>
          onChange({
            ...workspace,
            modeState: activateMode(workspace.modeState, modeId)
          })
        }
        onPatchMode={patchMode}
        onDuplicateMode={duplicateMode}
        onResetMode={resetActiveBuiltInMode}
        onDeleteMode={deleteMode}
      />

      <nav className={cx('aw-tabs')} aria-label="观众工作区">
        <button
          type="button"
          className={cx(tab === 'personas' && 'active')}
          onClick={() => setTab('personas')}
        >
          <Users size={15} />
          人格阵容
        </button>
        <button
          type="button"
          className={cx(tab === 'memes' && 'active')}
          onClick={() => setTab('memes')}
        >
          <Library size={15} />
          成长梗库
        </button>
      </nav>

      {tab === 'personas' ? (
        <div className={cx('aw-persona-layout')} data-audience-persona-layout>
          <PersonaList
            personas={personaRows}
            activeMode={activeMode}
            selectedPersonaId={selectedPersonaId}
            search={personaSearch}
            structureLocked={structureLocked}
            onSearchChange={setPersonaSearch}
            onAdd={addCustomPersona}
            onChoose={choosePersona}
            onParticipationChange={setPersonaParticipation}
            onWeightChange={setPersonaWeight}
          />
          <PersonaEditor
            activeMode={activeMode}
            draft={personaDraft}
            selectedBasePersona={selectedBasePersona}
            error={personaError}
            editorTab={editorTab}
            markdownDraft={markdownDraft}
            structureLocked={structureLocked}
            builtInPersonaIds={BUILT_IN_PERSONA_IDS}
            setDraft={setPersonaDraft}
            setEditorTab={setEditorTab}
            setMarkdownDraft={setMarkdownDraft}
            onDelete={deleteCustomPersona}
            onReset={resetPersona}
            onApplyMarkdown={applyMarkdown}
            onSave={savePersona}
            onParticipationChange={setPersonaParticipation}
          />
        </div>
      ) : (
        <div className={cx('aw-meme-layout')}>
          <MemeList
            entries={visibleMemes}
            selectedMemeId={selectedMeme?.id}
            search={memeSearch}
            filter={memeFilter}
            stats={stats}
            sourceLabels={MEME_SOURCE_LABELS}
            statusLabels={MEME_STATUS_LABELS}
            onSearchChange={setMemeSearch}
            onFilterChange={setMemeFilter}
            onAdd={addManualMeme}
            onSelect={setSelectedMemeId}
          />
          <MemeEditor
            memes={workspace.memes}
            selectedMeme={selectedMeme}
            draft={memeDraft}
            error={memeError}
            sourceLabels={MEME_SOURCE_LABELS}
            setDraft={setMemeDraft}
            onMutate={mutateMemes}
            onRestore={restoreSelectedMeme}
            onSave={saveMeme}
          />
        </div>
      )}
    </section>
  )
}
