import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { createInitialAudienceWorkspace } from '../../../../shared/audience'
import { PersonaList } from './PersonaList'

describe('PersonaList viewer allocation preview', () => {
  it('renders Hamilton instance counts and the exact total', () => {
    const workspace = createInitialAudienceWorkspace()
    const mode = {
      ...workspace.modeState.modes[0],
      viewerCount: 5,
      personaIds: workspace.personas.slice(0, 3).map((persona) => persona.id),
      personaWeights: {
        [workspace.personas[0].id]: 1,
        [workspace.personas[1].id]: 1,
        [workspace.personas[2].id]: 1
      }
    }
    const html = renderToStaticMarkup(
      <PersonaList
        personas={workspace.personas.slice(0, 3)}
        allPersonas={workspace.personas}
        activeMode={mode}
        selectedPersonaId={workspace.personas[0].id}
        search=""
        structureLocked={false}
        onSearchChange={() => undefined}
        onAdd={() => undefined}
        onChoose={() => undefined}
        onParticipationChange={() => undefined}
        onWeightChange={() => undefined}
      />
    )

    expect(html).toContain('实例 2')
    expect(html).toContain('实例 1')
    expect(html).toContain('共 5 个 Viewer')
  })
})
