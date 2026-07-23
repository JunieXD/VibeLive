import { useCallback, useEffect, useRef } from 'react'
import { AppShell, type AppShellStatusItem } from './app/AppShell'
import { AudienceWorkspace } from './AudienceWorkspace'
import { LiveView } from './features/live/LiveView'
import { SettingsView } from './features/settings/SettingsView'
import { SourcePickerDialog } from './features/source-picker/SourcePickerDialog'
import { useActivityFeed } from './hooks/useActivityFeed'
import { useAudienceWorkspacePersistence } from './hooks/useAudienceWorkspacePersistence'
import { useDemoBarrage } from './hooks/useDemoBarrage'
import { useElapsedTime } from './hooks/useElapsedTime'
import { useMediaController } from './hooks/useMediaController'
import { useModelConfig } from './hooks/useModelConfig'
import { useOverlaySettings } from './hooks/useOverlaySettings'
import { useVisualPipeline } from './hooks/useVisualPipeline'
import {
  selectActiveView,
  selectDispatchSession,
  selectSession,
  selectSetActiveView,
  useControlStore
} from './store/controlStore'

export function App(): React.JSX.Element {
  const activeView = useControlStore(selectActiveView)
  const setActiveView = useControlStore(selectSetActiveView)
  const session = useControlStore(selectSession)
  const dispatchSession = useControlStore(selectDispatchSession)
  const sessionStartedRef = useRef<() => void>(() => undefined)
  const handleSessionStarted = useCallback(() => sessionStartedRef.current(), [])

  const activityFeed = useActivityFeed()
  const audience = useAudienceWorkspacePersistence({
    onSystemActivity: activityFeed.appendSystemActivity
  })
  const media = useMediaController({
    sessionStatus: session.status,
    dispatchSession,
    onSystemActivity: activityFeed.appendSystemActivity,
    onSessionStarted: handleSessionStarted
  })
  const barrage = useDemoBarrage({
    workspace: audience.workspace,
    setWorkspace: audience.setWorkspace,
    sessionStatus: session.status,
    overlayVisible: media.overlayVisible,
    showOverlay: media.showOverlay,
    message: activityFeed.message,
    setMessage: activityFeed.setMessage,
    appendAudienceActivity: activityFeed.appendAudienceActivity,
    appendUserActivity: activityFeed.appendUserActivity,
    clearAudienceActivity: activityFeed.clearAudienceActivity
  })
  const visualPipeline = useVisualPipeline({
    sessionStatus: session.status,
    visualSettings: media.visualSettings,
    captureStream: media.captureStream,
    cameraStream: media.cameraStream,
    captureStreamRef: media.captureStreamRef,
    cameraStreamRef: media.cameraStreamRef,
    videoRef: media.videoRef,
    cameraVideoRef: media.cameraVideoRef
  })
  const elapsedSeconds = useElapsedTime(session.status)
  const overlay = useOverlaySettings()
  const modelConfig = useModelConfig()

  useEffect(() => {
    sessionStartedRef.current = () => barrage.emitBarrage('直播开始了，先热个场。')
  }, [barrage.emitBarrage])

  const statusItems: AppShellStatusItem[] = [
    {
      id: 'screen',
      label: `屏幕 ${media.captureStatus}${
        media.screenPermission === 'denied' || media.screenPermission === 'restricted'
          ? ' · 权限受限'
          : ''
      }`,
      tone: media.captureStream ? 'online' : 'offline'
    },
    {
      id: 'camera',
      label: `摄像头 ${media.cameraStatus}`,
      tone: media.cameraStream ? 'online' : 'offline'
    },
    {
      id: 'microphone',
      label: `麦克风 ${media.microphoneStatus}`,
      tone: media.microphoneReady ? 'online' : 'offline'
    },
    {
      id: 'visual',
      label:
        visualPipeline.status === 'ready'
          ? '图像 · 已就绪'
          : visualPipeline.status === 'compression-failed'
            ? '图像 · 压缩失败'
            : '图像 · 等待后端接入',
      tone:
        visualPipeline.status === 'ready'
          ? 'online'
          : visualPipeline.status === 'compression-failed'
            ? 'warning'
            : 'offline'
    }
  ]

  return (
    <>
      <AppShell
        activeView={activeView}
        onViewChange={setActiveView}
        sessionStatus={session.status}
        audienceCount={barrage.activeAudience.length}
        modeName={barrage.runtime.mode.name}
        elapsedSeconds={elapsedSeconds}
        barrageTotal={barrage.barrageTotal}
        statusItems={statusItems}
      >
        {activeView === 'live' && (
          <LiveView
            session={session}
            stage={{
              session,
              effectiveVisualMode: media.effectiveVisualMode,
              visualSettings: media.visualSettings,
              setVisualSettings: media.setVisualSettings,
              selectedSource: media.selectedSource,
              captureStream: media.captureStream,
              cameraStream: media.cameraStream,
              cameras: media.cameras,
              cameraEnabled: media.cameraEnabled,
              mediaTransitioning: media.mediaTransitioning,
              isSessionActive: media.isSessionActive,
              canStart: media.canStart,
              goLiveBusy: media.goLiveBusy,
              overlayVisible: media.overlayVisible,
              barrageTotal: barrage.barrageTotal,
              microphoneLevel: media.microphoneLevel,
              message: activityFeed.message,
              pipPreviewStyle: media.pipPreviewStyle,
              videoRef: media.videoRef,
              cameraVideoRef: media.cameraVideoRef,
              compositeCanvasRef: visualPipeline.compositeCanvasRef,
              onOpenSourcePicker: () => media.setSourcePickerOpen(true),
              onChangeVisualMode: media.changeVisualMode,
              onToggleGoLive: media.toggleGoLive,
              onTogglePause: media.togglePause,
              onClearBarrage: barrage.clearBarrage,
              onToggleOverlay: media.toggleOverlay,
              onMessageChange: activityFeed.setMessage,
              onSendUserMessage: barrage.sendUserMessage
            }}
            chat={{
              activity: activityFeed.activity,
              chatListRef: activityFeed.chatListRef
            }}
            mixer={{
              captureStream: media.captureStream,
              cameraStream: media.cameraStream,
              captureStatus: media.captureStatus,
              cameraStatus: media.cameraStatus,
              microphoneLevel: media.microphoneLevel,
              visualSettings: media.visualSettings,
              lastFrameBytes: visualPipeline.lastFrameBytes,
              lastFrameOverTarget: visualPipeline.lastFrameOverTarget,
              lastVisualBatchAt: visualPipeline.lastBatchAt,
              visualPipelineStatus: visualPipeline.status
            }}
            devices={{
              session,
              microphones: media.microphones,
              selectedMicrophoneId: media.selectedMicrophoneId,
              microphoneReady: media.microphoneReady,
              microphonePermission: media.microphonePermission,
              cameras: media.cameras,
              cameraStream: media.cameraStream,
              cameraEnabled: media.cameraEnabled,
              cameraPermission: media.cameraPermission,
              cameraDeviceId: media.visualSettings.cameraDeviceId,
              isSessionActive: media.isSessionActive,
              mediaTransitioning: media.mediaTransitioning,
              onChangeMicrophone: media.changeMicrophone,
              onRequestMicrophoneAccess: media.requestMicrophoneAccess,
              onChangeCamera: media.changeCamera,
              onToggleCamera: media.toggleCamera
            }}
          />
        )}

        {activeView === 'audience' && (
          <AudienceWorkspace
            workspace={audience.workspace}
            sessionStatus={session.status}
            persistenceReady={audience.ready}
            persistenceIssue={audience.loadError}
            onChange={audience.setWorkspace}
            onRetryLoad={() => void audience.retry()}
            onResetRejected={audience.reset}
          />
        )}

        {activeView === 'settings' && (
          <SettingsView
            modelBaseUrl={modelConfig.baseUrl}
            modelName={modelConfig.model}
            apiKey={modelConfig.apiKey}
            configNotice={modelConfig.notice}
            overlaySettings={overlay.settings}
            overlayTargets={overlay.targets}
            overlaySettingsNotice={overlay.notice}
            onModelBaseUrlChange={modelConfig.setBaseUrl}
            onModelNameChange={modelConfig.setModel}
            onApiKeyChange={modelConfig.setApiKey}
            onSaveModelConfig={() => void modelConfig.save()}
            onOverlaySettingsChange={overlay.updateSettings}
            onPreviewBarrage={(mode) => void barrage.previewBarrage(mode)}
          />
        )}
      </AppShell>

      {media.sourcePickerOpen && (
        <SourcePickerDialog
          onClose={() => media.setSourcePickerOpen(false)}
          onSelect={media.chooseSource}
        />
      )}
    </>
  )
}
