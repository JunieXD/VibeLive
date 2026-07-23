import {
  Archive,
  ArchiveRestore,
  Copy,
  FileText,
  Library,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  Trash2,
  Undo2,
  Users
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  BASE_PERSONAS,
  activateMode,
  archiveMeme,
  disableMeme,
  duplicateModeAsCustom,
  findMemeConflict,
  normalizeMemeText,
  parsePersonaMarkdown,
  resetBuiltInMode,
  restoreMeme,
  serializePersonaMarkdown,
  setMemePinned,
  undoAutomaticMeme,
  validatePersona,
  type AudienceMode,
  type AudienceWorkspaceState,
  type MemeEntry,
  type MemeStatus,
  type Persona,
  type PersonaOverride
} from '../../shared/audience'
import type { SessionStatus } from '../../shared/session'
import './audience-workspace.css'

type AudienceWorkspaceProps = {
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

function clampActivityValue(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.min(99, Math.max(0, Math.trunc(parsed))) : 0
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

function IconButton({
  title,
  disabled,
  danger,
  onClick,
  children
}: {
  title: string
  disabled?: boolean
  danger?: boolean
  onClick(): void
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <button
      type="button"
      className={`aw-icon-button${danger ? ' danger' : ''}`}
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
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
    return <div className="audience-workspace aw-empty">没有可用的观众模式</div>
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
      className={`audience-workspace${persistenceReady ? '' : ' has-persistence-alert'}`}
    >
      {!persistenceReady && (
        <div
          className="aw-persistence-alert"
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
            <div className="aw-persistence-actions">
              <button type="button" onClick={onRetryLoad}>
                <RefreshCw size={14} />
                重试加载
              </button>
              <button type="button" className="danger" onClick={onResetRejected}>
                <RotateCcw size={14} />
                重置默认
              </button>
            </div>
          )}
        </div>
      )}
      <header className="aw-mode-toolbar">
        <div className="aw-mode-main">
          <label>
            <span>观众模式</span>
            <select
              value={activeMode.id}
              disabled={structureLocked}
              onChange={(event) =>
                onChange({
                  ...workspace,
                  modeState: activateMode(workspace.modeState, event.target.value)
                })
              }
            >
              {workspace.modeState.modes.map((mode) => (
                <option key={mode.id} value={mode.id}>
                  {mode.name}
                </option>
              ))}
            </select>
          </label>
          <div className="aw-mode-copy">
            {activeMode.builtIn ? (
              <strong>{activeMode.name}</strong>
            ) : (
              <input
                className="aw-mode-name-input"
                aria-label="自定义模式名称"
                value={modeNameDraft}
                disabled={structureLocked}
                onChange={(event) => setModeNameDraft(event.target.value)}
                onBlur={commitModeName}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') event.currentTarget.blur()
                  if (event.key === 'Escape') {
                    setModeNameDraft(activeMode.name)
                    event.currentTarget.blur()
                  }
                }}
              />
            )}
            <span>{activeMode.description}</span>
          </div>
        </div>
        <div className="aw-mode-controls">
          <label className="aw-range-control">
            <span>导演活跃目标</span>
            <input
              type="number"
              min={0}
              max={99}
              value={activeMode.baseActivity[0]}
              onChange={(event) =>
                patchMode({
                  baseActivity: [
                    Math.min(
                      clampActivityValue(event.target.value),
                      activeMode.baseActivity[1]
                    ),
                    activeMode.baseActivity[1]
                  ]
                })
              }
            />
            <b>至</b>
            <input
              type="number"
              min={0}
              max={99}
              value={activeMode.baseActivity[1]}
              onChange={(event) =>
                patchMode({
                  baseActivity: [
                    activeMode.baseActivity[0],
                    Math.max(
                      clampActivityValue(event.target.value),
                      activeMode.baseActivity[0]
                    )
                  ]
                })
              }
            />
          </label>
          <label className="aw-range-control">
            <span>导演爆点目标</span>
            <input
              type="number"
              min={0}
              max={99}
              value={activeMode.burstLimit[0]}
              onChange={(event) =>
                patchMode({
                  burstLimit: [
                    Math.min(
                      clampActivityValue(event.target.value),
                      activeMode.burstLimit[1]
                    ),
                    activeMode.burstLimit[1]
                  ]
                })
              }
            />
            <b>至</b>
            <input
              type="number"
              min={0}
              max={99}
              value={activeMode.burstLimit[1]}
              onChange={(event) =>
                patchMode({
                  burstLimit: [
                    activeMode.burstLimit[0],
                    Math.max(
                      clampActivityValue(event.target.value),
                      activeMode.burstLimit[0]
                    )
                  ]
                })
              }
            />
          </label>
          <label>
            <span>冷场策略</span>
            <select
              value={activeMode.ambience}
              onChange={(event) =>
                patchMode({ ambience: event.target.value as AudienceMode['ambience'] })
              }
            >
              <option value="natural">自然静默</option>
              <option value="continuous">持续暖场</option>
            </select>
          </label>
          <div className="aw-toolbar-actions">
            <IconButton title="复制为自定义模式" disabled={structureLocked} onClick={duplicateMode}>
              <Copy size={15} />
            </IconButton>
            {activeMode.builtIn ? (
              <IconButton
                title="重置内置模式"
                disabled={structureLocked}
                onClick={resetActiveBuiltInMode}
              >
                <RotateCcw size={15} />
              </IconButton>
            ) : (
              <IconButton
                title="删除自定义模式"
                danger
                disabled={structureLocked}
                onClick={deleteMode}
              >
                <Trash2 size={15} />
              </IconButton>
            )}
          </div>
        </div>
      </header>

      <nav className="aw-tabs" aria-label="观众工作区">
        <button
          type="button"
          className={tab === 'personas' ? 'active' : ''}
          onClick={() => setTab('personas')}
        >
          <Users size={15} />
          人格阵容
        </button>
        <button
          type="button"
          className={tab === 'memes' ? 'active' : ''}
          onClick={() => setTab('memes')}
        >
          <Library size={15} />
          成长梗库
        </button>
      </nav>

      {tab === 'personas' ? (
        <div className="aw-persona-layout">
          <aside className="aw-directory">
            <div className="aw-search-row">
              <Search size={14} />
              <input
                value={personaSearch}
                placeholder="搜索人格"
                onChange={(event) => setPersonaSearch(event.target.value)}
              />
              <IconButton title="新建自定义人格" disabled={structureLocked} onClick={addCustomPersona}>
                <Plus size={15} />
              </IconButton>
            </div>
            <div className="aw-persona-list">
              {personaRows.map((persona) => {
                const resolved = effectivePersona(persona, activeMode)
                const included = activeMode.personaIds.includes(persona.id)
                const participating = included && resolved.enabled
                return (
                  <article
                    key={persona.id}
                    className={`aw-persona-row${persona.id === selectedPersonaId ? ' selected' : ''}`}
                    onClick={() => choosePersona(persona.id)}
                  >
                    <button
                      type="button"
                      className="aw-persona-identity"
                      onClick={() => choosePersona(persona.id)}
                    >
                      <i style={{ backgroundColor: resolved.color }}>{resolved.initials}</i>
                      <span>
                        <strong>{resolved.name}</strong>
                        <small>{resolved.role}</small>
                      </span>
                    </button>
                    <label className="aw-switch" title={participating ? '停用人格' : '启用人格'}>
                      <input
                        type="checkbox"
                        checked={participating}
                        onChange={() => setPersonaParticipation(persona.id, !participating)}
                      />
                      <span aria-hidden="true" />
                    </label>
                    <label className="aw-weight" title="当前模式权重">
                      <input
                        type="range"
                        min={1}
                        max={5}
                        step={1}
                        disabled={!participating}
                        value={participating ? activeMode.personaWeights[persona.id] ?? 1 : 0}
                        onChange={(event) => setPersonaWeight(persona.id, Number(event.target.value))}
                      />
                      <b>{participating ? activeMode.personaWeights[persona.id] ?? 1 : 0}</b>
                    </label>
                  </article>
                )
              })}
            </div>
          </aside>

          <main className={`aw-editor${personaError ? ' has-validation' : ''}`}>
            {personaDraft && selectedBasePersona ? (
              <>
                <div className="aw-editor-heading">
                  <div>
                    <strong>{personaDraft.name}</strong>
                    <span>
                      {BUILT_IN_PERSONA_IDS.has(personaDraft.id) ? '内置人格' : '自定义人格'} ·{' '}
                      {activeMode.personaOverrides[personaDraft.id] ? '当前模式已覆盖' : '使用基础配置'}
                    </span>
                  </div>
                  <div className="aw-segmented">
                    <button
                      type="button"
                      className={editorTab === 'form' ? 'active' : ''}
                      onClick={() => setEditorTab('form')}
                    >
                      <SlidersHorizontal size={14} />
                      表单
                    </button>
                    <button
                      type="button"
                      className={editorTab === 'markdown' ? 'active' : ''}
                      onClick={() => {
                        setMarkdownDraft(serializePersonaMarkdown(personaDraft))
                        setEditorTab('markdown')
                      }}
                    >
                      <FileText size={14} />
                      Markdown
                    </button>
                  </div>
                  <div className="aw-editor-actions">
                    {!BUILT_IN_PERSONA_IDS.has(personaDraft.id) && (
                      <IconButton
                        title="删除自定义人格"
                        danger
                        disabled={structureLocked}
                        onClick={deleteCustomPersona}
                      >
                        <Trash2 size={15} />
                      </IconButton>
                    )}
                    <IconButton
                      title="重置当前模式的人格覆盖"
                      disabled={structureLocked || !activeMode.personaOverrides[personaDraft.id]}
                      onClick={resetPersona}
                    >
                      <RotateCcw size={15} />
                    </IconButton>
                    <button
                      type="button"
                      className="aw-save-button"
                      disabled={structureLocked}
                      onClick={editorTab === 'markdown' ? applyMarkdown : savePersona}
                    >
                      <Save size={15} />
                      保存覆盖
                    </button>
                  </div>
                </div>
                {personaError && <div className="aw-validation">{personaError}</div>}
                {editorTab === 'markdown' ? (
                  <textarea
                    className="aw-markdown"
                    value={markdownDraft}
                    disabled={structureLocked}
                    spellCheck={false}
                    onChange={(event) => setMarkdownDraft(event.target.value)}
                  />
                ) : (
                  <div className="aw-form">
                    <div className="aw-form-grid four">
                      <label>
                        <span>稳定 ID</span>
                        <input value={personaDraft.id} disabled />
                      </label>
                      <label>
                        <span>名称</span>
                        <input
                          value={personaDraft.name}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({ ...personaDraft, name: event.target.value })
                          }
                        />
                      </label>
                      <label>
                        <span>缩写</span>
                        <input
                          value={personaDraft.initials}
                          maxLength={4}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({ ...personaDraft, initials: event.target.value })
                          }
                        />
                      </label>
                      <label>
                        <span>色标</span>
                        <div className="aw-color-input">
                          <input
                            type="color"
                            value={personaDraft.color}
                            disabled={structureLocked}
                            onChange={(event) =>
                              setPersonaDraft({ ...personaDraft, color: event.target.value })
                            }
                          />
                          <input
                            value={personaDraft.color}
                            disabled={structureLocked}
                            onChange={(event) =>
                              setPersonaDraft({ ...personaDraft, color: event.target.value })
                            }
                          />
                        </div>
                      </label>
                    </div>
                    <div className="aw-form-grid two">
                      <label>
                        <span>角色</span>
                        <input
                          value={personaDraft.role}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({ ...personaDraft, role: event.target.value })
                          }
                        />
                      </label>
                      <label>
                        <span>Traits（逗号或换行）</span>
                        <input
                          value={personaDraft.traits.join(', ')}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({ ...personaDraft, traits: splitList(event.target.value) })
                          }
                        />
                      </label>
                    </div>
                    <label>
                      <span>说话方式</span>
                      <textarea
                        value={personaDraft.speechStyle}
                        disabled={structureLocked}
                        onChange={(event) =>
                          setPersonaDraft({ ...personaDraft, speechStyle: event.target.value })
                        }
                      />
                    </label>
                    <div className="aw-form-grid two">
                      <label>
                        <span>触发偏好</span>
                        <textarea
                          value={personaDraft.triggerPreferences.join('\n')}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({
                              ...personaDraft,
                              triggerPreferences: splitList(event.target.value)
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>避免模式</span>
                        <textarea
                          value={personaDraft.avoidPatterns.join('\n')}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({
                              ...personaDraft,
                              avoidPatterns: splitList(event.target.value)
                            })
                          }
                        />
                      </label>
                    </div>
                    <div className="aw-bias-grid">
                      {(
                        [
                          ['silenceBias', '静默'],
                          ['burstBias', '爆发'],
                          ['repetitionBias', '复读']
                        ] as const
                      ).map(([field, label]) => (
                        <label key={field}>
                          <span>
                            {label} <b>{personaDraft[field]}</b>
                          </span>
                          <input
                            type="range"
                            min={0}
                            max={4}
                            step={1}
                            value={personaDraft[field]}
                            disabled={structureLocked}
                            onChange={(event) =>
                              setPersonaDraft({
                                ...personaDraft,
                                [field]: Number(event.target.value) as 0 | 1 | 2 | 3 | 4
                              })
                            }
                          />
                        </label>
                      ))}
                      <label>
                        <span>冷却（毫秒）</span>
                        <input
                          type="number"
                          min={0}
                          step={500}
                          value={personaDraft.cooldownMs}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({
                              ...personaDraft,
                              cooldownMs: Number(event.target.value)
                            })
                          }
                        />
                      </label>
                      <label>
                        <span>单次条数</span>
                        <select
                          value={personaDraft.maxCommentsPerDecision}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({
                              ...personaDraft,
                              maxCommentsPerDecision: Number(event.target.value) as 1 | 2
                            })
                          }
                        >
                          <option value={1}>1</option>
                          <option value={2}>2</option>
                        </select>
                      </label>
                    </div>
                    <div className="aw-form-grid two">
                      <label>
                        <span>Content flags（逗号或换行）</span>
                        <input
                          value={personaDraft.contentFlags.join(', ')}
                          disabled={structureLocked}
                          onChange={(event) =>
                            setPersonaDraft({
                              ...personaDraft,
                              contentFlags: splitList(event.target.value)
                            })
                          }
                        />
                      </label>
                      <div className="aw-toggle-field">
                        <span>参与状态</span>
                        <label className="aw-switch">
                          <input
                            type="checkbox"
                            checked={
                              activeMode.personaIds.includes(personaDraft.id) &&
                              personaDraft.enabled
                            }
                            onChange={(event) =>
                              setPersonaParticipation(personaDraft.id, event.target.checked)
                            }
                          />
                          <span aria-hidden="true" />
                          <em>
                            {activeMode.personaIds.includes(personaDraft.id) &&
                            personaDraft.enabled
                              ? '参与'
                              : '停用'}
                          </em>
                        </label>
                      </div>
                    </div>
                    <label>
                      <span>Behavior</span>
                      <textarea
                        className="aw-behavior"
                        value={personaDraft.behavior}
                        disabled={structureLocked}
                        onChange={(event) =>
                          setPersonaDraft({ ...personaDraft, behavior: event.target.value })
                        }
                      />
                    </label>
                  </div>
                )}
              </>
            ) : (
              <div className="aw-empty">选择一个人格开始编辑</div>
            )}
          </main>
        </div>
      ) : (
        <div className="aw-meme-layout">
          <aside className="aw-meme-list-pane">
            <div className="aw-meme-stats">
              <span><b>{stats.active}</b> 启用</span>
              <span><b>{stats.new}</b> 新梗</span>
              <span><b>{stats.archived}</b> 归档</span>
            </div>
            <div className="aw-search-row">
              <Search size={14} />
              <input
                value={memeSearch}
                placeholder="搜索文本、家族或人格"
                onChange={(event) => setMemeSearch(event.target.value)}
              />
              <select
                value={memeFilter}
                aria-label="梗状态筛选"
                onChange={(event) => setMemeFilter(event.target.value as MemeFilter)}
              >
                <option value="all">全部</option>
                <option value="active">启用</option>
                <option value="new">新梗</option>
                <option value="inactive">停用</option>
                <option value="archived">归档</option>
              </select>
              <IconButton title="手动新增梗" onClick={addManualMeme}>
                <Plus size={15} />
              </IconButton>
            </div>
            <div className="aw-meme-list">
              {visibleMemes.map((entry) => (
                <button
                  type="button"
                  key={entry.id}
                  className={`aw-meme-row${entry.id === selectedMeme?.id ? ' selected' : ''}`}
                  onClick={() => setSelectedMemeId(entry.id)}
                >
                  <span className="aw-meme-row-title">
                    {entry.pinned && <Pin size={11} />}
                    <strong>{entry.text}</strong>
                  </span>
                  <span className="aw-tags">
                    <i className={entry.source}>{entry.source === 'automatic' ? '导演' : '用户'}</i>
                    {entry.sourceKinds.map((kind) => (
                      <i key={kind}>{MEME_SOURCE_LABELS[kind]}</i>
                    ))}
                    <i>{MEME_STATUS_LABELS[entry.status]}</i>
                  </span>
                </button>
              ))}
              {visibleMemes.length === 0 && <div className="aw-empty">当前筛选下没有梗</div>}
            </div>
          </aside>

          <main className="aw-meme-editor">
            {selectedMeme && memeDraft ? (
              <>
                <div className="aw-editor-heading">
                  <div>
                    <strong>{selectedMeme.source === 'automatic' ? '导演自动梗' : '手动梗'}</strong>
                    <span>
                      {selectedMeme.sourceKinds.map((kind) => MEME_SOURCE_LABELS[kind]).join(' · ')}
                      {' · '}使用 {selectedMeme.usageCount} 次
                    </span>
                  </div>
                  <div className="aw-editor-actions">
                    <IconButton
                      title={selectedMeme.pinned ? '取消置顶' : '置顶'}
                      disabled={selectedMeme.status === 'archived'}
                      onClick={() =>
                        mutateMemes(
                          setMemePinned(workspace.memes, selectedMeme.id, !selectedMeme.pinned)
                        )
                      }
                    >
                      {selectedMeme.pinned ? <PinOff size={15} /> : <Pin size={15} />}
                    </IconButton>
                    {selectedMeme.source === 'automatic' && (
                      <IconButton
                        title="撤销自动梗"
                        onClick={() =>
                          mutateMemes(undoAutomaticMeme(workspace.memes, selectedMeme.id))
                        }
                      >
                        <Undo2 size={15} />
                      </IconButton>
                    )}
                    {selectedMeme.status === 'archived' ? (
                      <IconButton
                        title="恢复梗"
                        onClick={restoreSelectedMeme}
                      >
                        <ArchiveRestore size={15} />
                      </IconButton>
                    ) : (
                      <IconButton
                        title="归档梗"
                        onClick={() => mutateMemes(archiveMeme(workspace.memes, selectedMeme.id))}
                      >
                        <Archive size={15} />
                      </IconButton>
                    )}
                    <IconButton
                      title="删除梗"
                      danger
                      onClick={() =>
                        mutateMemes(workspace.memes.filter((entry) => entry.id !== selectedMeme.id))
                      }
                    >
                      <Trash2 size={15} />
                    </IconButton>
                    <button
                      type="button"
                      className="aw-save-button"
                      onClick={saveMeme}
                    >
                      <Save size={15} />
                      保存
                    </button>
                  </div>
                </div>
                <div className="aw-meme-meta">
                  <span>{selectedMeme.createdBy === 'director' ? '导演生成' : '用户创建'}</span>
                  <span>{new Date(selectedMeme.createdAt).toLocaleString()}</span>
                  <span>rev {selectedMeme.revision}</span>
                  <label className="aw-switch">
                    <input
                      type="checkbox"
                      checked={selectedMeme.status === 'active'}
                      disabled={selectedMeme.status === 'archived'}
                      onChange={() =>
                        selectedMeme.status === 'active'
                          ? mutateMemes(disableMeme(workspace.memes, selectedMeme.id))
                          : restoreSelectedMeme()
                      }
                    />
                    <span aria-hidden="true" />
                    <em>{selectedMeme.status === 'active' ? '启用' : '停用'}</em>
                  </label>
                </div>
                <div className="aw-form aw-meme-form">
                  {memeError && <div className="aw-validation">{memeError}</div>}
                  <label>
                    <span>弹幕文本</span>
                    <textarea
                      value={memeDraft.text}
                      onChange={(event) => setMemeDraft({ ...memeDraft, text: event.target.value })}
                    />
                  </label>
                  <div className="aw-form-grid two">
                    <label>
                      <span>梗家族</span>
                      <input
                        value={memeDraft.familyKey}
                        onChange={(event) =>
                          setMemeDraft({ ...memeDraft, familyKey: event.target.value })
                        }
                      />
                    </label>
                    <label>
                      <span>人格标签</span>
                      <input
                        value={memeDraft.personaTags}
                        onChange={(event) =>
                          setMemeDraft({ ...memeDraft, personaTags: event.target.value })
                        }
                      />
                    </label>
                  </div>
                  <label>
                    <span>证据摘要（最多 160 字）</span>
                    <textarea
                      maxLength={160}
                      value={memeDraft.evidenceSummary}
                      onChange={(event) =>
                        setMemeDraft({ ...memeDraft, evidenceSummary: event.target.value })
                      }
                    />
                  </label>
                </div>
              </>
            ) : (
              <div className="aw-empty">选择或新增一个梗</div>
            )}
          </main>
        </div>
      )}
    </section>
  )
}
