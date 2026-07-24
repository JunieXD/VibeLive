import { AudioLines, Camera, CameraOff, KeyRound, Mic, MicOff, Volume2 } from 'lucide-react'
import { SelectDropdown } from '../../components/SelectDropdown'
import type { LiveDeviceStripProps } from './liveTypes'

export function LiveDeviceStrip(props: LiveDeviceStripProps): React.JSX.Element {
  const {
    session,
    microphones,
    selectedMicrophoneId,
    microphoneEnabled,
    microphoneReady,
    microphonePermission,
    systemAudioEnabled,
    systemAudioSupported,
    systemAudioReady,
    systemAudioStatus,
    cameras,
    cameraStream,
    cameraEnabled,
    cameraPermission,
    cameraDeviceId,
    isSessionActive,
    mediaTransitioning,
    onChangeMicrophone,
    onRequestMicrophoneAccess,
    onToggleMicrophone,
    onToggleSystemAudio,
    onChangeCamera,
    onToggleCamera
  } = props

  const cameraControlDisabled =
    mediaTransitioning ||
    session.status === 'paused' ||
    session.status === 'starting' ||
    session.status === 'stopping'

  return (
    <section className="device-strip">
      <div className="device-grid">
        <div className="device-control">
          <Mic size={16} />
          <div>
            <label htmlFor="microphone">麦克风</label>
            <SelectDropdown
              id="microphone"
              ariaLabel="麦克风"
              value={selectedMicrophoneId}
              options={
                microphones.length === 0
                  ? [{ value: '', label: '未授权设备' }]
                  : microphones.map((device, index) => ({
                      value: device.deviceId,
                      label: device.label || `麦克风 ${index + 1}`
                    }))
              }
              onChange={(deviceId) => void onChangeMicrophone(deviceId)}
              disabled={
                mediaTransitioning ||
                session.status === 'starting' ||
                session.status === 'stopping'
              }
            />
          </div>
          <div className="device-actions">
            <button
              className="icon-button"
              type="button"
              aria-label={microphoneReady ? '重新检测麦克风' : '授权并检测麦克风'}
              title={microphoneReady ? '重新检测麦克风' : '授权并检测麦克风'}
              disabled={!microphoneEnabled || isSessionActive || mediaTransitioning}
              onClick={() => void onRequestMicrophoneAccess()}
            >
              <Volume2 size={15} />
            </button>
            <button
              id="microphone-toggle"
              className={`ghost-button ${microphoneReady ? 'camera-active' : ''}`}
              type="button"
              role="switch"
              aria-checked={microphoneEnabled}
              aria-label={microphoneEnabled ? '关闭麦克风' : '开启麦克风'}
              disabled={
                mediaTransitioning ||
                session.status === 'starting' ||
                session.status === 'stopping'
              }
              onClick={() => void onToggleMicrophone()}
            >
              {microphoneEnabled ? <MicOff size={15} /> : <Mic size={15} />}
              {microphoneEnabled ? '关闭' : '开启'}
            </button>
          </div>
        </div>

        <div className="device-control">
          <AudioLines size={16} />
          <div>
            <label htmlFor="system-audio-toggle">系统声音 · 推荐</label>
            <span className="device-status">
              {systemAudioSupported ? systemAudioStatus : '仅 Windows 支持'}
            </span>
          </div>
          <button
            id="system-audio-toggle"
            className={`ghost-button ${systemAudioReady ? 'camera-active' : ''}`}
            type="button"
            role="switch"
            aria-checked={systemAudioEnabled}
            disabled={!systemAudioSupported || mediaTransitioning || session.status === 'starting' || session.status === 'stopping'}
            onClick={() => void onToggleSystemAudio()}
          >
            <AudioLines size={15} />
            {systemAudioEnabled ? '关闭' : '开启'}
          </button>
        </div>

        <div className="device-control">
          <Camera size={16} />
          <div>
            <label htmlFor="camera">摄像头</label>
            <SelectDropdown
              id="camera"
              ariaLabel="摄像头"
              value={cameraDeviceId}
              options={
                cameras.length === 0
                  ? [{ value: '', label: '未检测到设备' }]
                  : cameras.map((device, index) => ({
                      value: device.deviceId,
                      label: device.label || `摄像头 ${index + 1}`
                    }))
              }
              onChange={(deviceId) => void onChangeCamera(deviceId)}
              disabled={cameraControlDisabled}
            />
          </div>
          <button
            className={`ghost-button ${cameraStream ? 'camera-active' : ''}`}
            type="button"
            disabled={cameraControlDisabled}
            onClick={() => void onToggleCamera()}
          >
            {cameraStream ? <CameraOff size={15} /> : <Camera size={15} />}
            {cameraStream ? '关闭摄像头' : cameraEnabled ? '重新开启' : '开启摄像头'}
          </button>
        </div>
      </div>
      <div className="privacy-stack">
        <div className="privacy-note">
          <KeyRound size={14} />
          {!microphoneEnabled
            ? '麦克风已关闭'
            : microphonePermission === 'denied' || microphonePermission === 'restricted'
            ? '系统麦克风权限受限'
            : microphoneReady
              ? '正在进行本地音量检测'
              : '授权后可实时检测麦克风音量'}
        </div>
        <div className="privacy-note">
          <AudioLines size={14} />
          麦克风与系统原始音频仅发送给 StepFun、不持久化；模型只接收最终转写
        </div>
        <div className="privacy-note">
          <Camera size={14} />
          {cameraPermission === 'denied' || cameraPermission === 'restricted'
            ? '系统摄像头权限受限'
            : cameraStream
              ? '摄像头视频仅保存在内存'
              : cameraEnabled
                ? '当前视觉模式未使用摄像头'
                : '摄像头默认关闭'}
        </div>
      </div>
    </section>
  )
}
