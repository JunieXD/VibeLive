import {
  Eye,
  MessageCircleMore,
  Minus,
  Send,
  Trash2,
  X
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  appendAudienceMessage,
  appendHostMessage,
  messagesPerMinute,
  uniqueAudienceCount,
  type FloatingChatMessage
} from './floating-chat-state'
import { ViewerAvatar } from '../control/components/ViewerAvatar'

const MAX_MESSAGE_LENGTH = 200

function formatMessageTime(createdAt: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  }).format(createdAt)
}

export function App(): React.JSX.Element {
  const [messages, setMessages] = useState<FloatingChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const audienceCount = useMemo(() => uniqueAudienceCount(messages), [messages])
  const currentRate = useMemo(
    () => messagesPerMinute(messages, Date.now()),
    [messages]
  )

  useEffect(() => {
    const removeBarrage = window.advxFloatingChat.onBarrage((event) => {
      setMessages((current) => appendAudienceMessage(current, event))
    })
    const removeClear = window.advxFloatingChat.onClear(() => {
      setMessages([])
    })

    return () => {
      removeBarrage()
      removeClear()
    }
  }, [])

  useEffect(() => {
    const list = listRef.current
    if (!list) return
    list.scrollTop = list.scrollHeight
  }, [messages])

  const submitDraft = async (): Promise<void> => {
    const text = draft.trim()
    if (!text || sending) return

    setSending(true)
    setSendError(null)
    try {
      await window.advxFloatingChat.submitText(text)
      setMessages((current) =>
        appendHostMessage(
          current,
          text,
          Date.now(),
          `host-${crypto.randomUUID()}`
        )
      )
      setDraft('')
    } catch (error) {
      setSendError(
        error instanceof Error && error.message
          ? error.message
          : '消息发送失败'
      )
    } finally {
      setSending(false)
    }
  }

  return (
    <main className="floating-chat-shell">
      <header className="titlebar">
        <div className="titlebar-brand">
          <MessageCircleMore size={18} aria-hidden="true" />
          <strong>直播互动</strong>
        </div>
        <div className="window-actions">
          <button
            type="button"
            title="最小化"
            aria-label="最小化"
            onClick={() => void window.advxFloatingChat.minimize()}
          >
            <Minus size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            title="关闭互动窗"
            aria-label="关闭互动窗"
            onClick={() => void window.advxFloatingChat.hide()}
          >
            <X size={19} aria-hidden="true" />
          </button>
        </div>
      </header>

      <section className="interaction-summary" aria-label="互动统计">
        <span title="互动观众">
          <Eye size={15} aria-hidden="true" />
          {audienceCount}
        </span>
        <span title="消息总数">
          <MessageCircleMore size={15} aria-hidden="true" />
          {messages.length}
        </span>
        <button
          type="button"
          title="清空消息"
          aria-label="清空消息"
          disabled={messages.length === 0}
          onClick={() => void window.advxFloatingChat.clear()}
        >
          <Trash2 size={15} aria-hidden="true" />
        </button>
      </section>

      <div
        className="message-list"
        ref={listRef}
        role="log"
        aria-live="polite"
        aria-label="直播互动消息"
      >
        {messages.length === 0 ? (
          <div className="empty-state">
            <MessageCircleMore size={26} aria-hidden="true" />
            <span>等待直播互动</span>
          </div>
        ) : (
          messages.map((message) => (
            <article className={`message-row message-row--${message.source}`} key={message.id}>
              <ViewerAvatar
                avatarSeed={message.audienceId ?? message.author}
                colorSeed={message.color ?? message.author}
                className="message-avatar"
              />
              <div className="message-body">
                <div className="message-meta">
                  <strong style={message.color ? { color: message.color } : undefined}>
                    {message.author}
                  </strong>
                  <time dateTime={new Date(message.createdAt).toISOString()}>
                    {formatMessageTime(message.createdAt)}
                  </time>
                </div>
                <p>{message.text}</p>
              </div>
            </article>
          ))
        )}
      </div>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault()
          void submitDraft()
        }}
      >
        <div className="composer-field">
          <input
            aria-label="互动消息"
            value={draft}
            maxLength={MAX_MESSAGE_LENGTH}
            placeholder="输入文字"
            onChange={(event) => setDraft(event.target.value)}
          />
          <span aria-hidden="true">
            {draft.length}/{MAX_MESSAGE_LENGTH}
          </span>
        </div>
        <button
          type="submit"
          title="发送"
          aria-label="发送"
          disabled={sending || draft.trim() === ''}
        >
          <Send size={17} aria-hidden="true" />
        </button>
      </form>

      <footer className="connection-status">
        <span className="status-dot" aria-hidden="true" />
        <span>{sendError ?? '直播互动中'}</span>
        <span className="status-rate">{currentRate} 条/分钟</span>
      </footer>
    </main>
  )
}
