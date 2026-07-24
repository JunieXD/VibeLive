import type {
  AudienceModeState,
  AudienceRuntimeSnapshot,
  Persona,
  RuntimePersona
} from './types'
import { compileViewerPool } from './viewer-allocation'
import { createPersonaTemplate } from './canonical'

export function compileAudienceRuntimeSnapshot(
  personas: readonly Persona[],
  modeState: AudienceModeState,
  sessionSeed = 'default'
): AudienceRuntimeSnapshot {
  const mode = modeState.modes.find((candidate) => candidate.id === modeState.activeModeId)
  if (!mode) throw new Error(`Active audience mode does not exist: ${modeState.activeModeId}`)

  const templates = personas.map((persona) =>
    'documentVersion' in persona ? persona : createPersonaTemplate(persona)
  )
  const runtimePersonas: readonly RuntimePersona[] = compileViewerPool(mode, templates, sessionSeed)

  return {
    mode: {
      id: mode.id,
      namespaceId: mode.namespaceId,
      revision: mode.revision,
      name: mode.name,
      description: mode.description,
      viewerCount: mode.viewerCount,
      normalResponseRange: [...mode.normalResponseRange],
      highlightResponseRange: [...mode.highlightResponseRange],
      ambience: mode.ambience,
      visualSettings: { ...mode.visualSettings },
      baseActivity: [...mode.normalResponseRange],
      burstLimit: [...mode.highlightResponseRange]
    },
    personas: runtimePersonas
  }
}
