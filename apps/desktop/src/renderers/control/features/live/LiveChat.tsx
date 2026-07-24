import { useEffect } from 'react'
import type { LiveChatProps } from './liveTypes'

export function LiveChat({
  activity,
  chatListRef,
  showHeading = true
}: LiveChatProps): React.JSX.Element {
  useEffect(() => {
    const list = chatListRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [activity, chatListRef])

  return (
    <section className={`chat-panel ${showHeading ? '' : 'chat-panel-no-heading'}`}>
      {showHeading && (
        <div className="panel-heading compact">
          <span className="panel-title">房间互动</span>
          <span className="chat-count">{activity.length}</span>
        </div>
      )}
      <div className="chat-list" ref={chatListRef}>
        {activity.map((item) => (
          <article className={`chat-item ${item.source}`} key={item.id}>
            <span
              className="chat-avatar"
              style={item.color ? { backgroundColor: item.color } : undefined}
            >
              {item.author.charAt(0)}
            </span>
            <div className="chat-content">
              <span
                className="chat-author"
                style={item.color ? { color: item.color } : undefined}
              >
                {item.author}
                {item.source === 'audience' && <em className="chat-tag">AI</em>}
              </span>
              <p>{item.text}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
