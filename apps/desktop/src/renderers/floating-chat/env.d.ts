/// <reference types="vite/client" />

import type { FloatingChatApi } from '../../shared/contracts'

declare global {
  interface Window {
    advxFloatingChat: FloatingChatApi
  }
}

export {}
