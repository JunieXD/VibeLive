import { Pencil, Plus, Search } from 'lucide-react'
import {
  materializePersonaTemplate,
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
  onChoose
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
          const viewerCount = activeMode.personaCounts[persona.id] ?? 0
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
              <span className={cx('aw-persona-allocation')}>
                {viewerCount} 人
              </span>
              <IconButton title={`编辑${resolved.name}`} onClick={() => onChoose(persona.id)}>
                <Pencil size={14} />
              </IconButton>
            </article>
          )
        })}
      </div>
      <footer className={cx('aw-viewer-allocation-total')}>
        共 {Object.values(activeMode.personaCounts).reduce((total, count) => total + count, 0)} 人
      </footer>
    </aside>
  )
}
