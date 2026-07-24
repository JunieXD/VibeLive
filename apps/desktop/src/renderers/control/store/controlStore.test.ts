import { beforeEach, describe, expect, it } from 'vitest'
import {
  selectActiveView,
  selectDispatchSession,
  selectSession,
  selectSessionError,
  selectSessionStatus,
  selectSetActiveView,
  useControlStore
} from './controlStore'

describe('control store', () => {
  beforeEach(() => {
    useControlStore.getState().reset()
  })

  it('exposes navigation state and actions through narrow selectors', () => {
    let state = useControlStore.getState()

    expect(selectActiveView(state)).toBe('live')
    expect(selectSessionStatus(state)).toBe('idle')
    expect(selectSessionError(state)).toBeNull()

    selectSetActiveView(state)('viewers')
    state = useControlStore.getState()
    expect(selectActiveView(state)).toBe('viewers')

    selectSetActiveView(state)('interaction')
    expect(selectActiveView(useControlStore.getState())).toBe('interaction')

    selectSetActiveView(state)('ai-calls')
    expect(selectActiveView(useControlStore.getState())).toBe('ai-calls')
  })

  it('uses the shared session transitions and ignores invalid actions', () => {
    const dispatchSession = selectDispatchSession(useControlStore.getState())

    dispatchSession({ type: 'start' })
    dispatchSession({ type: 'started' })
    dispatchSession({ type: 'pause' })
    expect(selectSession(useControlStore.getState())).toEqual({
      status: 'paused',
      error: null
    })

    const pausedSession = selectSession(useControlStore.getState())
    dispatchSession({ type: 'start' })
    expect(selectSession(useControlStore.getState())).toBe(pausedSession)

    dispatchSession({ type: 'resume' })
    dispatchSession({ type: 'stop' })
    dispatchSession({ type: 'stopped' })
    expect(selectSession(useControlStore.getState())).toEqual({
      status: 'idle',
      error: null
    })
  })
})
