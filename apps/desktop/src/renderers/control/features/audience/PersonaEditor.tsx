import { FileText, RotateCcw, Save, SlidersHorizontal, Trash2, X } from 'lucide-react'
import type { Dispatch, SetStateAction } from 'react'
import {
  materializePersonaTemplate,
  serializePersonaMarkdown,
  type AudienceMode,
  type Persona,
  type PersonaContent
} from '../../../../shared/audience'
import { SelectDropdown } from '../../components/SelectDropdown'
import { IconButton } from './IconButton'
import { cx } from './styles'

export type EditorTab = 'form' | 'markdown'

type PersonaEditorProps = {
  activeMode: AudienceMode
  draft: Persona | null
  selectedBasePersona: Persona | undefined
  error: string
  editorTab: EditorTab
  markdownDraft: string
  structureLocked: boolean
  builtInPersonaIds: ReadonlySet<string>
  setDraft: Dispatch<SetStateAction<Persona | null>>
  setEditorTab(tab: EditorTab): void
  setMarkdownDraft(value: string): void
  onClose(): void
  onDelete(): void
  onReset(): void
  onApplyMarkdown(): void
  onSave(): void
  onParticipationChange(personaId: string, enabled: boolean): void
  onWeightChange(personaId: string, weight: number): void
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function PersonaEditor({
  activeMode,
  draft,
  selectedBasePersona,
  error,
  editorTab,
  markdownDraft,
  structureLocked,
  builtInPersonaIds,
  setDraft,
  setEditorTab,
  setMarkdownDraft,
  onClose,
  onDelete,
  onReset,
  onApplyMarkdown,
  onSave,
  onParticipationChange,
  onWeightChange
}: PersonaEditorProps): React.JSX.Element {
  if (!draft || !selectedBasePersona) {
    return (
      <section className={cx('aw-editor')} data-audience-persona-editor>
        <div className={cx('aw-empty')}>选择一个人格开始编辑</div>
      </section>
    )
  }

  const update = (change: Partial<Omit<PersonaContent, 'id'>>): void =>
    setDraft(materializePersonaTemplate(draft, change))
  const participating = activeMode.personaIds.includes(draft.id) && draft.enabled
  const weight = activeMode.personaWeights[draft.id] ?? 1

  return (
    <section
      className={cx('aw-editor', Boolean(error) && 'has-validation')}
      data-audience-persona-editor
    >
      <div className={cx('aw-editor-heading')}>
        <div>
          <strong>{draft.name}</strong>
          <span>
            {builtInPersonaIds.has(draft.id) ? '内置人格' : '自定义人格'} ·{' '}
            {activeMode.personaOverrides[draft.id] ? '当前模式已覆盖' : '使用基础配置'}
          </span>
        </div>
        <div className={cx('aw-segmented')}>
          <button
            type="button"
            className={cx(editorTab === 'form' && 'active')}
            onClick={() => setEditorTab('form')}
          >
            <SlidersHorizontal size={14} />
            表单
          </button>
          <button
            type="button"
            className={cx(editorTab === 'markdown' && 'active')}
            onClick={() => {
              setMarkdownDraft(serializePersonaMarkdown(draft))
              setEditorTab('markdown')
            }}
          >
            <FileText size={14} />
            Markdown
          </button>
        </div>
        <div className={cx('aw-editor-actions')}>
          {!builtInPersonaIds.has(draft.id) && (
            <IconButton title="删除自定义人格" danger disabled={structureLocked} onClick={onDelete}>
              <Trash2 size={15} />
            </IconButton>
          )}
          <IconButton
            title="重置当前模式的人格覆盖"
            disabled={structureLocked || !activeMode.personaOverrides[draft.id]}
            onClick={onReset}
          >
            <RotateCcw size={15} />
          </IconButton>
          <button
            type="button"
            className={cx('aw-save-button')}
            disabled={structureLocked}
            onClick={editorTab === 'markdown' ? onApplyMarkdown : onSave}
          >
            <Save size={15} />
            保存覆盖
          </button>
          <IconButton title="关闭人格编辑" onClick={onClose}>
            <X size={16} />
          </IconButton>
        </div>
      </div>
      {error && <div className={cx('aw-validation')}>{error}</div>}
      {editorTab === 'markdown' ? (
        <textarea
          className={cx('aw-markdown')}
          value={markdownDraft}
          disabled={structureLocked}
          spellCheck={false}
          onChange={(event) => setMarkdownDraft(event.target.value)}
        />
      ) : (
        <div className={cx('aw-form')}>
          <div className={cx('aw-form-grid', 'four')}>
            <label>
              <span>稳定 ID</span>
              <input value={draft.id} disabled />
            </label>
            <label>
              <span>名称</span>
              <input
                value={draft.name}
                disabled={structureLocked}
                onChange={(event) => update({ name: event.target.value })}
              />
            </label>
            <label>
              <span>缩写</span>
              <input
                value={draft.initials}
                maxLength={4}
                disabled={structureLocked}
                onChange={(event) => update({ initials: event.target.value })}
              />
            </label>
            <label>
              <span>色标</span>
              <div className={cx('aw-color-input')}>
                <input
                  type="color"
                  value={draft.color}
                  disabled={structureLocked}
                  onChange={(event) => update({ color: event.target.value })}
                />
                <input
                  value={draft.color}
                  disabled={structureLocked}
                  onChange={(event) => update({ color: event.target.value })}
                />
              </div>
            </label>
          </div>
          <div className={cx('aw-form-grid', 'two')}>
            <label>
              <span>角色</span>
              <input
                value={draft.role}
                disabled={structureLocked}
                onChange={(event) => update({ role: event.target.value })}
              />
            </label>
            <label>
              <span>Traits（逗号或换行）</span>
              <input
                value={draft.traits.join(', ')}
                disabled={structureLocked}
                onChange={(event) => update({ traits: splitList(event.target.value) })}
              />
            </label>
          </div>
          <label>
            <span>说话方式</span>
            <textarea
              value={draft.speechStyle}
              disabled={structureLocked}
              onChange={(event) => update({ speechStyle: event.target.value })}
            />
          </label>
          <div className={cx('aw-form-grid', 'two')}>
            <label>
              <span>触发偏好</span>
              <textarea
                value={draft.triggerPreferences.join('\n')}
                disabled={structureLocked}
                onChange={(event) => update({ triggerPreferences: splitList(event.target.value) })}
              />
            </label>
            <label>
              <span>避免模式</span>
              <textarea
                value={draft.avoidPatterns.join('\n')}
                disabled={structureLocked}
                onChange={(event) => update({ avoidPatterns: splitList(event.target.value) })}
              />
            </label>
          </div>
          <details className={cx('aw-form-advanced')}>
            <summary>
              <SlidersHorizontal size={15} />
              高级参数
            </summary>
            <div className={cx('aw-bias-grid')}>
              {(
                [
                  ['silenceBias', '静默'],
                  ['burstBias', '爆发'],
                  ['repetitionBias', '复读']
                ] as const
              ).map(([field, label]) => (
                <label key={field}>
                  <span>
                    {label} <b>{draft[field]}</b>
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={4}
                    step={1}
                    value={draft[field]}
                    disabled={structureLocked}
                    onChange={(event) =>
                      update({ [field]: Number(event.target.value) as 0 | 1 | 2 | 3 | 4 })
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
                  value={draft.cooldownMs}
                  disabled={structureLocked}
                  onChange={(event) => update({ cooldownMs: Number(event.target.value) })}
                />
              </label>
              <label>
                <span>单次条数</span>
                <SelectDropdown
                  ariaLabel="单次评论条数"
                  compact
                  value={draft.maxCommentsPerDecision}
                  disabled={structureLocked}
                  options={[
                    { value: 1, label: '1' },
                    { value: 2, label: '2' }
                  ]}
                  onChange={(maxCommentsPerDecision) => update({ maxCommentsPerDecision })}
                />
              </label>
            </div>
          </details>
          <div className={cx('aw-form-grid', 'two')}>
            <label>
              <span>Content flags（逗号或换行）</span>
              <input
                value={draft.contentFlags.join(', ')}
                disabled={structureLocked}
                onChange={(event) => update({ contentFlags: splitList(event.target.value) })}
              />
            </label>
            <div className={cx('aw-toggle-field')}>
              <span>参与状态</span>
              <label className={cx('aw-switch')}>
                <input
                  type="checkbox"
                  checked={participating}
                  disabled={structureLocked}
                  aria-label={`${participating ? '停用' : '启用'}${draft.name}`}
                  onChange={(event) => onParticipationChange(draft.id, event.target.checked)}
                />
                <span aria-hidden="true" />
                <em>{participating ? '参与' : '停用'}</em>
              </label>
              <label className={cx('aw-editor-weight')}>
                <span>
                  权重 <b>{weight}</b>
                </span>
                <input
                  type="range"
                  min={1}
                  max={5}
                  step={1}
                  value={weight}
                  disabled={structureLocked || !participating}
                  aria-label={`${draft.name} 权重`}
                  aria-valuetext={`${weight}`}
                  onChange={(event) => onWeightChange(draft.id, Number(event.target.value))}
                />
              </label>
            </div>
          </div>
          <label>
            <span>Behavior</span>
            <textarea
              className={cx('aw-behavior')}
              value={draft.behavior}
              disabled={structureLocked}
              onChange={(event) => update({ behavior: event.target.value })}
            />
          </label>
        </div>
      )}
    </section>
  )
}
