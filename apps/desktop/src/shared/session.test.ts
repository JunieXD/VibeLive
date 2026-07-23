import { describe, expect, it } from 'vitest'
import { initialSessionState, sessionReducer } from './session'

describe('desktop sessionReducer', () => {
  it('runs the normal start, pause, resume and stop flow', () => {
    let state = sessionReducer(initialSessionState, { type: 'start' })
    state = sessionReducer(state, { type: 'started' })
    state = sessionReducer(state, { type: 'pause' })
    expect(state.status).toBe('paused')

    state = sessionReducer(state, { type: 'resume' })
    state = sessionReducer(state, { type: 'stop' })
    state = sessionReducer(state, { type: 'stopped' })
    expect(state).toEqual(initialSessionState)
  })

  it('does not start twice or resume an idle session', () => {
    const starting = sessionReducer(initialSessionState, { type: 'start' })
    expect(sessionReducer(starting, { type: 'start' })).toBe(starting)
    expect(sessionReducer(initialSessionState, { type: 'resume' })).toBe(initialSessionState)
  })

  it('keeps the control surface recoverable after a failure', () => {
    const failed = sessionReducer(initialSessionState, {
      type: 'fail',
      error: '模型连接失败'
    })
    expect(failed.status).toBe('error')
    expect(sessionReducer(failed, { type: 'stop' }).status).toBe('stopping')
  })
})
