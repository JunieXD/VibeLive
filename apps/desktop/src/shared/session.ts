export type SessionStatus = 'idle' | 'starting' | 'running' | 'paused' | 'stopping' | 'error'

export type SessionState = {
  readonly status: SessionStatus
  readonly error: string | null
}

export type SessionAction =
  | { type: 'start' }
  | { type: 'started' }
  | { type: 'pause' }
  | { type: 'resume' }
  | { type: 'stop' }
  | { type: 'stopped' }
  | { type: 'fail'; error: string }

export const initialSessionState: SessionState = {
  status: 'idle',
  error: null
}

export function sessionReducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case 'start':
      return state.status === 'idle' || state.status === 'error'
        ? { status: 'starting', error: null }
        : state
    case 'started':
      return state.status === 'starting' ? { status: 'running', error: null } : state
    case 'pause':
      return state.status === 'running' ? { ...state, status: 'paused' } : state
    case 'resume':
      return state.status === 'paused' ? { ...state, status: 'running' } : state
    case 'stop':
      return state.status !== 'idle' && state.status !== 'stopping'
        ? { ...state, status: 'stopping' }
        : state
    case 'stopped':
      return { status: 'idle', error: null }
    case 'fail':
      return { status: 'error', error: action.error }
  }
}
