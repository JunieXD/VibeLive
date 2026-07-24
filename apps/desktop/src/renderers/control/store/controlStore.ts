import { create } from 'zustand'
import {
  initialSessionState,
  sessionReducer,
  type SessionAction,
  type SessionState
} from '../../../shared/session'

export type ActiveView = 'live' | 'viewers' | 'audience' | 'settings'

export type ControlStore = {
  activeView: ActiveView
  session: SessionState
  setActiveView: (activeView: ActiveView) => void
  dispatchSession: (action: SessionAction) => void
  reset: () => void
}

const initialControlState = {
  activeView: 'live' as const,
  session: initialSessionState
}

export const useControlStore = create<ControlStore>()((set) => ({
  ...initialControlState,
  setActiveView: (activeView) => set({ activeView }),
  dispatchSession: (action) =>
    set((state) => {
      const session = sessionReducer(state.session, action)
      return session === state.session ? state : { session }
    }),
  reset: () => set(initialControlState)
}))

export const selectActiveView = (state: ControlStore) => state.activeView
export const selectSession = (state: ControlStore) => state.session
export const selectSessionStatus = (state: ControlStore) => state.session.status
export const selectSessionError = (state: ControlStore) => state.session.error
export const selectSetActiveView = (state: ControlStore) => state.setActiveView
export const selectDispatchSession = (state: ControlStore) => state.dispatchSession
