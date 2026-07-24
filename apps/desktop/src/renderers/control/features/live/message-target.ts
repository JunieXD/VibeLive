import type { RuntimeViewer } from '../../../../shared/backend-client'

export type MessageTarget = {
  kind: 'viewer' | 'persona'
  id: string
  label: string
  personaId?: string
}

export type SelectedMessageTarget = {
  target: MessageTarget
  start: number
  end: number
  tokenText: string
}

export type TargetedTextInput = {
  text: string
  targetViewerId?: string
  targetPersonaId?: string
}

export function suggestMentionTargets(
  input: string,
  targets: readonly MessageTarget[]
): readonly MessageTarget[] {
  const mention = input.match(/(?:^|\s)@([^\s]*)$/u)
  if (!mention) return []
  const query = mention[1].toLocaleLowerCase()
  return targets
    .filter((target) => !query || target.label.toLocaleLowerCase().includes(query))
    .slice(0, 8)
}

export function parseTargetedMessage(
  input: string,
  selection: SelectedMessageTarget | null
): TargetedTextInput {
  const trimmed = input.trim()
  if (!selection || !selectionMatches(input, selection)) return { text: trimmed }
  const text = `${input.slice(0, selection.start)}${input.slice(selection.end)}`.trim()
  return selection.target.kind === 'viewer'
    ? { text, targetViewerId: selection.target.id }
    : { text, targetPersonaId: selection.target.id }
}

export function insertSelectedTarget(
  input: string,
  target: MessageTarget
): { message: string; selection: SelectedMessageTarget } {
  const mention = /(^|\s)@[^\s]*$/u.exec(input)
  const prefixLength = mention?.[1].length ?? 0
  const start = mention ? mention.index + prefixLength : input.length
  const tokenText = `@${target.label} `
  const message = mention
    ? `${input.slice(0, start)}${tokenText}`
    : `${input}${input && !input.endsWith(' ') ? ' ' : ''}${tokenText}`
  const actualStart = mention ? start : message.length - tokenText.length
  return {
    message,
    selection: {
      target,
      start: actualStart,
      end: actualStart + tokenText.length,
      tokenText
    }
  }
}

export function updateSelectedTarget(
  previousInput: string,
  nextInput: string,
  selection: SelectedMessageTarget
): SelectedMessageTarget | null {
  if (!selectionMatches(previousInput, selection)) return null
  let prefix = 0
  while (
    prefix < previousInput.length &&
    prefix < nextInput.length &&
    previousInput[prefix] === nextInput[prefix]
  ) prefix += 1

  let suffix = 0
  while (
    suffix < previousInput.length - prefix &&
    suffix < nextInput.length - prefix &&
    previousInput[previousInput.length - 1 - suffix] === nextInput[nextInput.length - 1 - suffix]
  ) suffix += 1

  const previousChangeEnd = previousInput.length - suffix
  const nextChangeEnd = nextInput.length - suffix
  let nextSelection = selection
  if (previousChangeEnd <= selection.start) {
    const shift = nextChangeEnd - previousChangeEnd
    nextSelection = {
      ...selection,
      start: selection.start + shift,
      end: selection.end + shift
    }
  } else if (prefix < selection.end) {
    return null
  }
  return selectionMatches(nextInput, nextSelection) ? nextSelection : null
}

export function selectionMatches(
  input: string,
  selection: SelectedMessageTarget
): boolean {
  return (
    selection.start >= 0 &&
    selection.end === selection.start + selection.tokenText.length &&
    input.slice(selection.start, selection.end) === selection.tokenText
  )
}

export function buildRuntimeMessageTargets(
  viewers: readonly RuntimeViewer[],
  personas: readonly { id: string; name: string }[]
): readonly MessageTarget[] {
  const activePersonaIds = new Set(viewers.map((viewer) => viewer.persona_id))
  return [
    ...viewers
      .filter((viewer) => viewer.lifecycle_state === 'active')
      .map((viewer) => ({
        kind: 'viewer' as const,
        id: viewer.viewer_instance_id,
        label: viewer.display_name,
        personaId: viewer.persona_id
      })),
    ...personas
      .filter((persona) => activePersonaIds.has(persona.id))
      .map((persona) => ({
        kind: 'persona' as const,
        id: persona.id,
        label: persona.name
      }))
  ]
}
