import { Camera, CameraOff, KeyRound, Mic, Volume2 } from 'lucide-react'
import type { LiveDeviceStripProps } from './liveTypes'

export function LiveDeviceStrip(props: LiveDeviceStripProps): React.JSX.Element {
  const {
    session,
    microphones,
    selectedMicrophoneId,
    microphoneReady,
    microphonePermission,
    cameras,
    cameraStream,
    cameraEnabled,
    cameraPermission,
    cameraDeviceId,
    isSessionActive,
    mediaTransitioning,
    onChangeMicrophone,
    onRequestMicrophoneAccess,
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
            <select
              id="microphone"
              value={selectedMicrophoneId}
              onChange={(event) => void onChangeMicrophone(event.target.value)}
              disabled={isSessionActive || mediaTransitioning}
            >
              {microphones.length === 0 && <option value="">未授权设备</option>}
              {microphones.map((device, index) => (
                <option key={device.deviceId} value={device.deviceId}>
                  {device.label || `麦克风 ${index + 1}`}
                </option>
              ))}
            </select>
          </div>
          <button
            className="ghost-button"
            type="button"
            disabled={isSessionActive || mediaTransitioning}
            onClick={() => void onRequestMicrophoneAccess()}
          >
            <Volume2 size={15} />
            {mediaTransitioning ? '检测中...' : microphoneReady ? '重新检测' : '授权并检测'}
          </button>
        </div>

        <div className="device-control">
          <Camera size={16} />
          <div>
            <label htmlFor="camera">摄像头</label>
            <select
              id="camera"
              value={cameraDeviceId}
              onChange={(event) => void onChangeCamera(event.target.value)}
              disabled={cameraControlDisabled}
            >
              {cameras.length === 0 && <option value="">未检测到设备</option>}
              {cameras.map((device, index) => (
                <option key={device.deviceId || `camera-${index}`} value={device.deviceId}>
                  {device.label || `摄像头 ${index + 1}`}
                </option>
              ))}
            </select>
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
          {microphonePermission === 'denied' || microphonePermission === 'restricted'
            ? '系统麦克风权限受限'
            : microphoneReady
              ? '正在进行本地音量检测'
              : '授权后可实时检测麦克风音量'}
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
