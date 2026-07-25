import { Brain, Library, RefreshCw, RotateCcw, ShieldAlert, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  BASE_PERSONAS,
  activateMode,
  duplicateModeAsCustom,
  materializePersonaTemplate,
  parsePersonaMarkdown,
  resetBuiltInMode,
  reviseAudienceMode,
  serializePersonaMarkdown,
  totalViewerCount,
  validatePersona,
  type AudienceMode,
  type AudienceWorkspaceState,
  type Persona,
  type PersonaOverride
} from '../../../../shared/audience'
import type { SessionStatus } from '../../../../shared/session'
import { ModeToolbar } from './ModeToolbar'
import {
  AudienceRuntimeToolbar,
  type AudienceRuntimeToolbarProps
} from './AudienceRuntimeToolbar'
import { PersonaEditor } from './PersonaEditor'
import { PersonaList } from './PersonaList'
import {
  BackendMemePanel,
  RoomMemoryPanel,
  type SharedBrainController
} from './SharedBrainPanels'
import { cx } from './styles'

export type AudienceWorkspaceProps = {
  workspace: AudienceWorkspaceState
  sessionStatus: SessionStatus
  persistenceReady: boolean
  persistenceIssue: string | null
  onChange(next: AudienceWorkspaceState): void
  onRetryLoad(): void
  onResetRejected(): void
  runtimeControl: AudienceRuntimeToolbarProps
  sharedBrain: SharedBrainController
  sharedBrainAvailable: boolean
}

type WorkspaceTab = 'personas' | 'memes' | 'memories'
type EditorTab = 'form' | 'markdown'
const BUILT_IN_PERSONA_IDS = new Set(BASE_PERSONAS.map((persona) => persona.id))
const STRUCTURE_LOCKED_STATUSES = new Set<SessionStatus>(['starting', 'stopping'])

function createStableId(prefix: string): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replaceAll('-', '').slice(0, 12)
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`
  return `${prefix}-${random}`
}

function effectivePersona(base: Persona, mode: AudienceMode): Persona {
  return materializePersonaTemplate(base, mode.personaOverrides[base.id])
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

export function AudienceWorkspace({
  workspace,
  sessionStatus,
  persistenceReady,
  persistenceIssue,
  onChange,
  onRetryLoad,
  onResetRejected,
  runtimeControl,
  sharedBrain,
  sharedBrainAvailable
}: AudienceWorkspaceProps): React.JSX.Element {
  const [tab, setTab] = useState<WorkspaceTab>('personas')
  const [editorTab, setEditorTab] = useState<EditorTab>('form')
  const [personaEditorOpen, setPersonaEditorOpen] = useState(false)
  const [personaSearch, setPersonaSearch] = useState('')
  const [selectedPersonaId, setSelectedPersonaId] = useState(
    workspace.personas[0]?.id ?? ''
  )
  const [personaDraft, setPersonaDraft] = useState<Persona | null>(null)
  const [markdownDraft, setMarkdownDraft] = useState('')
  const [personaError, setPersonaError] = useState('')
  const [modeNameDraft, setModeNameDraft] = useState('')

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

  useEffect(() => {
    if (!personaEditorOpen) return
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setPersonaEditorOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [personaEditorOpen])

  if (!activeMode) {
    return (
      <div className={cx('audience-workspace', 'aw-empty')} data-audience-workspace>
        没有可用的观众模式
      </div>
    )
  }

  const patchMode = (change: Partial<AudienceMode>): void => {
    onChange(updateActiveMode(workspace, (mode) => reviseAudienceMode(mode, change)))
  }

  const commitModeName = (): void => {
    const name = modeNameDraft.trim()
    if (!name) {
      setModeNameDraft(activeMode.name)
      return
    }
    if (name !== activeMode.name) patchMode({ name })
  }

  const setPersonaCount = (personaId: string, value: number): void => {
    const count = Number.isFinite(value) ? Math.min(32, Math.max(0, Math.trunc(value))) : 0
    const personaCounts = { ...activeMode.personaCounts, [personaId]: count }
    const viewerCount = totalViewerCount({ ...activeMode, personaCounts })
    if (viewerCount < 1) {
      setPersonaError('模式至少需要 1 位观众')
      return
    }
    if (viewerCount > 32) {
      setPersonaError('一个模式最多只能有 32 位观众')
      return
    }
    setPersonaError('')
    patchMode({
      personaCounts,
      normalResponseRange: [
        Math.min(activeMode.normalResponseRange[0], viewerCount),
        Math.min(activeMode.normalResponseRange[1], viewerCount)
      ],
      highlightResponseRange: [
        Math.min(activeMode.highlightResponseRange[0], viewerCount),
        Math.min(activeMode.highlightResponseRange[1], viewerCount)
      ]
    })
  }

  const choosePersona = (personaId: string): void => {
    setSelectedPersonaId(personaId)
    setEditorTab('form')
    setPersonaEditorOpen(true)
  }

  const persistPersonaOverride = (base: Persona, next: Persona): void => {
    const override = personaOverride(base, next)
    const personaOverrides = { ...activeMode.personaOverrides }
    if (Object.keys(override).length === 0) delete personaOverrides[next.id]
    else personaOverrides[next.id] = override
    patchMode({ personaOverrides })
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
    patchMode({ personaOverrides })
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
        personaCounts: { ...mode.personaCounts, [id]: 0 }
      })),
      personas: [...workspace.personas, persona]
    })
    setSelectedPersonaId(id)
    setPersonaEditorOpen(true)
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
      const personaCounts = { ...mode.personaCounts }
      const personaOverrides = { ...mode.personaOverrides }
      delete personaCounts[personaId]
      delete personaOverrides[personaId]
      return {
        ...mode,
        personaCounts,
        personaOverrides
      }
    })
    if (modes.some((mode) => totalViewerCount(mode) === 0)) {
      setPersonaError('请先为每个模式保留至少 1 位其他观众，再删除该人格')
      return
    }
    onChange({
      ...workspace,
      personas: workspace.personas.filter((persona) => persona.id !== personaId),
      modeState: { ...workspace.modeState, modes }
    })
    setSelectedPersonaId(workspace.personas.find((persona) => persona.id !== personaId)?.id ?? '')
    setPersonaEditorOpen(false)
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
      }
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

  const personaRows = workspace.personas.filter((persona) => {
    const query = personaSearch.trim().toLocaleLowerCase()
    return (
      !query ||
      `${persona.name} ${persona.initials} ${persona.role} ${persona.traits.join(' ')}`
        .toLocaleLowerCase()
        .includes(query)
    )
  })
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
      <AudienceRuntimeToolbar {...runtimeControl} />
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
          onClick={() => {
            setPersonaEditorOpen(false)
            setTab('memes')
          }}
        >
          <Library size={15} />
          成长梗库
        </button>
        <button
          type="button"
          className={cx(tab === 'memories' && 'active')}
          onClick={() => {
            setPersonaEditorOpen(false)
            setTab('memories')
          }}
        >
          <Brain size={15} />
          长期记忆
        </button>
      </nav>

      {tab === 'personas' ? (
        <div className={cx('aw-persona-layout')} data-audience-persona-layout>
          <PersonaList
            personas={personaRows}
            activeMode={activeMode}
            selectedPersonaId={personaEditorOpen ? selectedPersonaId : ''}
            search={personaSearch}
            structureLocked={structureLocked}
            onSearchChange={setPersonaSearch}
            onAdd={addCustomPersona}
            onChoose={choosePersona}
            onViewerCountChange={setPersonaCount}
          />
        </div>
      ) : tab === 'memes' ? (
        <BackendMemePanel brain={sharedBrain} available={sharedBrainAvailable} />
      ) : (
        <RoomMemoryPanel brain={sharedBrain} available={sharedBrainAvailable} />
      )}

      {tab === 'personas' && personaEditorOpen && (
        <div className={cx('aw-floating-layer')} data-audience-editor-layer>
          <button
            type="button"
            className={cx('aw-floating-backdrop')}
            aria-label="关闭人格编辑"
            onClick={() => setPersonaEditorOpen(false)}
          />
          <div
            className={cx('aw-floating-panel')}
            role="dialog"
            aria-modal="true"
            aria-label={personaDraft ? `编辑 ${personaDraft.name}` : '编辑人格'}
          >
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
              onClose={() => setPersonaEditorOpen(false)}
              onDelete={deleteCustomPersona}
              onReset={resetPersona}
              onApplyMarkdown={applyMarkdown}
              onSave={savePersona}
              onViewerCountChange={setPersonaCount}
            />
          </div>
        </div>
      )}
    </section>
  )
}
