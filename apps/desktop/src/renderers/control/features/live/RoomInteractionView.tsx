import { AtSign, MessageSquareText, Send, Users } from 'lucide-react'
import { LiveChat } from './LiveChat'
import type { RoomInteractionViewProps } from './liveTypes'

export function RoomInteractionView({
  session,
  audienceSessionActive,
  audienceCount,
  activity,
  chatListRef,
  message,
  messageSending,
  targetSuggestions,
  onMessageChange,
  onSelectMessageTarget,
  onSendUserMessage
}: RoomInteractionViewProps): React.JSX.Element {
  const canMessage = session.status === 'running' && audienceSessionActive
  const isLive = session.status === 'running'

  return (
    <section className="room-interaction">
      <header className="room-interaction-header">
        <div>
          <div className="room-interaction-title">
            <MessageSquareText size={19} aria-hidden="true" />
            <h2>房间互动</h2>
          </div>
        </div>
        <div className="room-interaction-statuses" aria-label="互动状态">
          <span className={isLive ? 'room-status live' : 'room-status'}>
            <i aria-hidden="true" />
            {isLive ? '直播中' : '未开播'}
          </span>
          <span className="room-status">
            <Users size={14} aria-hidden="true" />
            {audienceCount ?? '--'} 位在线
          </span>
        </div>
      </header>

      <div className="room-interaction-feed">
        <LiveChat activity={activity} chatListRef={chatListRef} showHeading={false} />
      </div>

      <div className="room-interaction-composer">
        <AtSign size={17} aria-hidden="true" />
        <input
          value={message}
          onChange={(event) => onMessageChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onSendUserMessage()
          }}
          placeholder={
            !audienceSessionActive
              ? '配置 Provider 后可与 AI 观众互动'
              : isLive
                ? '说点什么，AI 观众会回应你'
                : '开始直播后可发送'
          }
          disabled={!canMessage}
          aria-label="发送房间消息"
        />
        <button
          className="icon-button accent"
          type="button"
          title="发送"
          disabled={!canMessage || messageSending || message.trim() === ''}
          onClick={onSendUserMessage}
        >
          <Send size={16} />
        </button>
        {canMessage && targetSuggestions.length > 0 && (
          <div className="mention-menu" role="listbox" aria-label="选择消息目标">
            {targetSuggestions.map((target) => (
              <button
                type="button"
                key={`${target.kind}:${target.id}`}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onSelectMessageTarget(target)}
              >
                <strong>@{target.label}</strong>
                <span>{target.kind === 'viewer' ? 'Viewer' : 'Persona'}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
