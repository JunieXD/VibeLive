import { LiveChat } from './LiveChat'
import { LiveAudience } from './LiveAudience'
import { LiveDeviceStrip } from './LiveDeviceStrip'
import { LiveMixer } from './LiveMixer'
import { LiveStage } from './LiveStage'
import type { LiveViewProps } from './liveTypes'
import './live.css'

export type {
  LiveActivityItem,
  LiveChatProps,
  LiveAudienceProps,
  LiveDeviceStripProps,
  LiveMixerProps,
  LiveStageProps,
  LiveViewProps
} from './liveTypes'

export function LiveView({
  session,
  stage,
  chat,
  audience,
  mixer,
  devices
}: LiveViewProps): React.JSX.Element {
  return (
    <div className="live-view">
      {session.error && <div className="error-banner">{session.error}</div>}
      <div className="live-layout">
        <LiveStage {...stage} />
        <aside className="right-rail">
          <LiveChat {...chat} />
          <LiveAudience {...audience} />
          <LiveMixer {...mixer} />
        </aside>
      </div>
      <LiveDeviceStrip {...devices} />
    </div>
  )
}
