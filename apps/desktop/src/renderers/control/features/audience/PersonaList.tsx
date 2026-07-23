import { Plus, Search } from 'lucide-react'
import type { AudienceMode, Persona } from '../../../../shared/audience'
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
  onParticipationChange(personaId: string, enabled: boolean): void
  onWeightChange(personaId: string, weight: number): void
}

function effectivePersona(base: Persona, mode: AudienceMode): Persona {
  return { ...base, ...mode.personaOverrides[base.id], id: base.id }
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
  onParticipationChange,
  onWeightChange
}: PersonaListProps): React.JSX.Element {
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
          const included = activeMode.personaIds.includes(persona.id)
          const participating = included && resolved.enabled
          return (
            <article
              key={persona.id}
              data-audience-persona-row
              className={cx(
                'aw-persona-row',
                persona.id === selectedPersonaId && 'selected'
              )}
              onClick={() => onChoose(persona.id)}
            >
              <button
                type="button"
                className={cx('aw-persona-identity')}
                onClick={() => onChoose(persona.id)}
              >
                <i style={{ backgroundColor: resolved.color }}>{resolved.initials}</i>
                <span>
                  <strong>{resolved.name}</strong>
                  <small>{resolved.role}</small>
                </span>
              </button>
              <label
                className={cx('aw-switch')}
                data-audience-participation
                title={participating ? '停用人格' : '启用人格'}
              >
                <input
                  type="checkbox"
                  checked={participating}
                  onChange={() => onParticipationChange(persona.id, !participating)}
                />
                <span aria-hidden="true" />
              </label>
              <label className={cx('aw-weight')} title="当前模式权重">
                <input
                  type="range"
                  min={1}
                  max={5}
                  step={1}
                  disabled={!participating}
                  value={participating ? activeMode.personaWeights[persona.id] ?? 1 : 0}
                  onChange={(event) => onWeightChange(persona.id, Number(event.target.value))}
                />
                <b>{participating ? activeMode.personaWeights[persona.id] ?? 1 : 0}</b>
              </label>
            </article>
          )
        })}
      </div>
    </aside>
  )
}
