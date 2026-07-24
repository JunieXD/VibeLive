import { createInitialModeState } from './modes'
import { BASE_PERSONAS } from './presets'
import { compileAudienceRuntimeSnapshot } from './runtime'
import type { AudienceRuntimeSnapshot, AudienceWorkspaceState } from './types'

export function createInitialAudienceWorkspace(): AudienceWorkspaceState {
  return {
    version: 3,
    personas: BASE_PERSONAS,
    modeState: createInitialModeState()
  }
}

export function compileAudienceWorkspaceSnapshot(
  workspace: AudienceWorkspaceState
): AudienceRuntimeSnapshot {
  return compileAudienceRuntimeSnapshot(
    workspace.personas,
    workspace.modeState
  )
}
