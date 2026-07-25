import { Minus, Pencil, Plus, Search } from 'lucide-react'
import {
  materializePersonaTemplate,
  totalViewerCount,
  type AudienceMode,
  type Persona
} from '../../../../shared/audience'
import { IconButton } from './IconButton'
import { cx } from './styles'

type PersonaListProps = {
  personas: readonly Persona[]
  activeMode: AudienceMode
  selectedPersonaId: string
  search: string
  structureLocked: boolean
  onSearchChange(value: string): void
  onAdd(): void
  onChoose(personaId: string): void
  onViewerCountChange(personaId: string, count: number): void
}

function effectivePersona(base: Persona, mode: AudienceMode): Persona {
  return materializePersonaTemplate(base, mode.personaOverrides[base.id])
}

export function PersonaList({
  personas,
  activeMode,
  selectedPersonaId,
  search,
  structureLocked,
  onSearchChange,
  onAdd,
  onChoose,
  onViewerCountChange
}: PersonaListProps): React.JSX.Element {
  const viewerTotal = totalViewerCount(activeMode)

  return (
    <aside className={cx('aw-directory')}>
      <div className={cx('aw-search-row')}>
        <Search size={14} />
        <input
          value={search}
          placeholder="搜索人格"
          onChange={(event) => onSearchChange(event.target.value)}
        />
        <IconButton title="新建自定义人格" disabled={structureLocked} onClick={onAdd}>
          <Plus size={15} />
        </IconButton>
      </div>
      <div className={cx('aw-persona-list')}>
        {personas.map((persona) => {
          const resolved = effectivePersona(persona, activeMode)
          const viewerCount = activeMode.personaCounts[persona.id] ?? 0
          const minimumViewerCount = viewerCount > 0 && viewerTotal === 1 ? 1 : 0
          const maximumViewerCount = viewerCount + 32 - viewerTotal
          const canDecrease = !structureLocked && viewerCount > 0 && viewerTotal > 1
          const canIncrease = !structureLocked && viewerTotal < 32
          const updateViewerCount = (value: number): void => {
            const count = Number.isFinite(value)
              ? Math.min(maximumViewerCount, Math.max(minimumViewerCount, Math.trunc(value)))
              : minimumViewerCount
            onViewerCountChange(persona.id, count)
          }
          return (
            <article
              key={persona.id}
              data-audience-persona-row
              className={cx(
                'aw-persona-row',
                persona.id === selectedPersonaId && 'selected'
              )}
            >
              <button
                type="button"
                className={cx('aw-persona-identity')}
                data-audience-persona-open
                onClick={() => onChoose(persona.id)}
              >
                <i style={{ backgroundColor: resolved.color }}>{resolved.initials}</i>
                <span>
                  <strong>{resolved.name}</strong>
                  <small>{resolved.role}</small>
                </span>
              </button>
              <div className={cx('aw-persona-preview')}>
                <span>{resolved.behavior}</span>
              </div>
              <div
                className={cx('aw-persona-allocation')}
                role="group"
                aria-label={`${resolved.name}观众人数`}
              >
                <IconButton
                  title={`减少${resolved.name}的观众人数`}
                  disabled={!canDecrease}
                  onClick={() => updateViewerCount(viewerCount - 1)}
                >
                  <Minus size={14} />
                </IconButton>
                <input
                  className={cx('aw-persona-count-input')}
                  type="number"
                  min={minimumViewerCount}
                  max={maximumViewerCount}
                  step={1}
                  inputMode="numeric"
                  value={viewerCount}
                  disabled={structureLocked}
                  aria-label={`${resolved.name}观众人数`}
                  onChange={(event) => updateViewerCount(Number(event.target.value))}
                />
                <IconButton
                  title={`增加${resolved.name}的观众人数`}
                  disabled={!canIncrease}
                  onClick={() => updateViewerCount(viewerCount + 1)}
                >
                  <Plus size={14} />
                </IconButton>
              </div>
              <IconButton title={`编辑${resolved.name}`} onClick={() => onChoose(persona.id)}>
                <Pencil size={14} />
              </IconButton>
            </article>
          )
        })}
      </div>
      <footer className={cx('aw-viewer-allocation-total')}>
        共 {viewerTotal} 人
      </footer>
    </aside>
  )
}
