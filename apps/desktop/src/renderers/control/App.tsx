import { useEffect } from 'react'
import type { BackendConnectionState, ColorTheme } from '../../shared/contracts'
import { AppShell, type AppShellStatusItem } from './app/AppShell'
import { AudienceWorkspace } from './AudienceWorkspace'
import { LiveView } from './features/live/LiveView'
import { AiCallLog } from './features/ai-calls/AiCallLog'
import { SettingsView } from './features/settings/SettingsView'
import { ViewerManagementView } from './features/viewers/ViewerManagementView'
import { SourcePickerDialog } from './features/source-picker/SourcePickerDialog'
import { useActivityFeed } from './hooks/useActivityFeed'
import { useAudienceWorkspacePersistence } from './hooks/useAudienceWorkspacePersistence'
import { useAudienceRuntimeControl } from './hooks/useAudienceRuntimeControl'
import { useBackendRuntime } from './hooks/useBackendRuntime'
import { useColorTheme } from './hooks/useColorTheme'
import { useAudienceBarrage } from './hooks/useAudienceBarrage'
import { useElapsedTime } from './hooks/useElapsedTime'
import { useMediaController } from './hooks/useMediaController'
import { useModelConfig } from './hooks/useModelConfig'
import { useOverlaySettings } from './hooks/useOverlaySettings'
import { useVisualPipeline } from './hooks/useVisualPipeline'
import { useSharedBrain } from './hooks/useSharedBrain'
import { useLiveAudience } from './hooks/useLiveAudience'
import {
  selectActiveView,
  selectDispatchSession,
  selectSession,
  selectSetActiveView,
  useControlStore
} from './store/controlStore'

const backendConnectionLabels: Record<BackendConnectionState, string> = {
  starting: '启动中',
  connecting: '连接中',
  connected: '已连接',
  disconnected: '已断开',
  failed: '启动失败'
}

export type AppProps = {
  initialColorTheme: ColorTheme
}

export function App({ initialColorTheme }: AppProps): React.JSX.Element {
  const activeView = useControlStore(selectActiveView)
  const setActiveView = useControlStore(selectSetActiveView)
  const session = useControlStore(selectSession)
  const dispatchSession = useControlStore(selectDispatchSession)
  const { colorTheme, toggleColorTheme } = useColorTheme(initialColorTheme)
  const backend = useBackendRuntime()
  const modelConfig = useModelConfig({
    backendConnection: backend.connection,
    onBackendStatus: backend.applyStatus
  })
  const providerProfileAvailable =
    modelConfig.status?.modelApiKeyStored === true &&
    modelConfig.status?.asrApiKeyStored === true
  const activityFeed = useActivityFeed()
  const audience = useAudienceWorkspacePersistence({
    onSystemActivity: activityFeed.appendSystemActivity
  })
  const audienceRuntime = useAudienceRuntimeControl({
    workspace: audience.workspace,
    persistenceReady: audience.ready,
    savedFingerprint: audience.savedFingerprint,
    sessionId: backend.status?.session.sessionId,
    recoverableSessionId: backend.status?.recoverableRuntimeSessionId,
    backendConnected: backend.connection === 'connected',
    sessionStatus: session.status,
    onSystemActivity: activityFeed.appendSystemActivity
  })
  const media = useMediaController({
    sessionStatus: session.status,
    dispatchSession,
    onSystemActivity: activityFeed.appendSystemActivity,
    onSessionStarted: () => undefined,
    backendConnected: backend.connection === 'connected',
    providerProfileAvailable,
    onBackendSessionSnapshot: backend.applySessionSnapshot,
    audienceWorkspace: audience.workspace
  })
  const barrage = useAudienceBarrage({
    workspace: audience.workspace,
    runtimeViewers: audienceRuntime.runtime?.viewers ?? [],
    sessionStatus: session.status,
    audienceSessionActive: media.audienceSessionActive,
    overlayVisible: media.overlayVisible,
    showOverlay: media.showOverlay,
    message: activityFeed.message,
    setMessage: activityFeed.setMessage,
    appendAudienceActivity: activityFeed.appendAudienceActivity,
    appendUserActivity: activityFeed.appendUserActivity,
    appendSystemActivity: activityFeed.appendSystemActivity,
    clearAudienceActivity: activityFeed.clearAudienceActivity,
  })
  const liveAudience = useLiveAudience(
    backend.status?.session.sessionId ?? null,
    media.audienceSessionActive,
    backend.connection === 'connected'
  )
  const activeAudienceMode =
    audience.workspace.modeState.modes.find(
      (mode) => mode.id === audience.workspace.modeState.activeModeId
    ) ?? audience.workspace.modeState.modes[0]
  const sharedBrain = useSharedBrain({
    roomId: audienceRuntime.runtime?.room_id ?? null,
    namespaceId: activeAudienceMode?.namespaceId ?? null,
    enabled: backend.connection === 'connected' && Boolean(audienceRuntime.runtime)
  })
  const appliedFrameBundle =
    audienceRuntime.runtime?.canonical_runtime_spec.settings?.frame_bundle
  const appliedFramePolicy = appliedFrameBundle
    ? {
        ...activeAudienceMode.visualSettings,
        frameBundleSize: appliedFrameBundle.frame_bundle_size,
        frameWindowMs: appliedFrameBundle.frame_window_ms,
        frameSelectionStrategy: appliedFrameBundle.frame_selection_strategy,
        frameMaxDimension: appliedFrameBundle.frame_max_dimension,
        frameQuality: appliedFrameBundle.frame_quality / 100
      }
    : activeAudienceMode.visualSettings
  const visualPipeline = useVisualPipeline({
    sessionStatus: session.status,
    visualSettings: media.visualSettings,
    framePolicy: appliedFramePolicy,
    captureStream: media.captureStream,
    cameraStream: media.cameraStream,
    captureStreamRef: media.captureStreamRef,
    cameraStreamRef: media.cameraStreamRef,
    videoRef: media.capturePipelineVideoRef,
    cameraVideoRef: media.cameraPipelineVideoRef,
    deliveryEnabled: media.audienceSessionActive
  })
  const elapsedSeconds = useElapsedTime(
    session.status,
    backend.status?.session.startedAtMs ?? null
  )
  const overlay = useOverlaySettings()

  useEffect(() => {
    const status = backend.status
    if (status?.connection !== 'connected') return
    if (!media.audienceSessionActive && session.status !== 'idle') return
    dispatchSession({
      type: 'sync',
      status: status.session.state,
      error: status.session.state === 'error' ? '后端 Session 进入错误状态。' : null
    })
  }, [backend.status, dispatchSession, media.audienceSessionActive, session.status])

  const backendReady =
    backend.connection === 'connected' && (backend.status?.providersConfigured ?? false)
  const statusItems: AppShellStatusItem[] = [
    {
      id: 'backend',
      label: `后端 · ${backendConnectionLabels[backend.connection]}`,
      tone:
        backend.connection === 'connected'
          ? 'online'
          : backend.connection === 'failed'
            ? 'failed'
            : 'warning'
    },
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
          : visualPipeline.status === 'local-preview'
            ? '图像 · 本地预览'
          : visualPipeline.status === 'compression-failed'
            ? '图像 · 压缩失败'
            : '图像 · 等待后端接入',
      tone:
        visualPipeline.status === 'ready' || visualPipeline.status === 'local-preview'
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
        colorTheme={colorTheme}
        onColorThemeToggle={toggleColorTheme}
        sessionStatus={session.status}
        audienceCount={
          media.audienceSessionActive
            ? liveAudience.audience?.active_count ?? null
            : 0
        }
        modeName={activeAudienceMode?.name ?? '未选择'}
        elapsedSeconds={elapsedSeconds}
        barrageTotal={barrage.barrageTotal}
        statusItems={statusItems}
        roomId={audienceRuntime.runtime?.room_id ?? '未启动'}
        notice={
          backend.notice
            ? {
                ...backend.notice,
                tone: backend.connection === 'failed' ? 'failed' : 'info',
                action:
                  backend.connection === 'failed'
                    ? {
                        label: '重试',
                        busyLabel: '正在重试',
                        busy: backend.retrying,
                        onClick: () => void backend.retry()
                      }
                    : undefined
              }
            : null
        }
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
              audienceSessionActive: media.audienceSessionActive,
              overlayVisible: media.overlayVisible,
              barrageTotal: barrage.barrageTotal,
              microphoneLevel: media.microphoneLevel,
              message: activityFeed.message,
              messageSending: barrage.messageSending,
              providerProbeState: {
                backendConnected: backend.connection === 'connected',
                profileLoading: modelConfig.loading,
                profileSaved:
                  modelConfig.status?.modelApiKeyStored === true &&
                  modelConfig.status?.asrApiKeyStored === true,
                profileId: modelConfig.status?.providerProfileId ?? null,
                runtimeProviderReady: backend.status?.providersConfigured ?? false,
                probing: audienceRuntime.probing,
                probe: audienceRuntime.probe,
                error: audienceRuntime.probeError
              },
              targetSuggestions: barrage.targetSuggestions,
              pipPreviewStyle: media.pipPreviewStyle,
              videoRef: media.videoRef,
              cameraVideoRef: media.cameraVideoRef,
              onOpenSourcePicker: () => media.setSourcePickerOpen(true),
              onChangeVisualMode: media.changeVisualMode,
              onToggleGoLive: media.toggleGoLive,
              onTogglePause: media.togglePause,
              onClearBarrage: barrage.clearBarrage,
              onToggleOverlay: media.toggleOverlay,
              onMessageChange: barrage.changeMessage,
              onSelectMessageTarget: barrage.selectMessageTarget,
              onSendUserMessage: () => void barrage.sendUserMessage()
            }}
            chat={{
              activity: activityFeed.activity,
              chatListRef: activityFeed.chatListRef
            }}
            audience={{
              audience: liveAudience.audience,
              audienceLoading: liveAudience.audienceLoading,
              audienceError: liveAudience.audienceError,
              operationError: liveAudience.operationError,
              pendingViewerId: liveAudience.pendingViewerId,
              onViewAll: () => setActiveView('viewers'),
              onRetryAudience: liveAudience.refresh,
              onDismissOperationError: liveAudience.clearOperationError,
              onMute: liveAudience.mute,
              onUnmute: liveAudience.unmute,
              onKick: liveAudience.kick
            }}
            mixer={{
              captureStream: media.captureStream,
              cameraStream: media.cameraStream,
              captureStatus: media.captureStatus,
              cameraStatus: media.cameraStatus,
              microphoneLevel: media.microphoneLevel,
              asrReady: backendReady,
              asrStatus: backendReady
                ? '已就绪'
                : backend.connection === 'connected'
                  ? '等待配置'
                  : '等待后端',
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

        {activeView === 'viewers' && (
          <ViewerManagementView
            sessionStatus={session.status}
            audience={liveAudience.audience}
            audienceLoading={liveAudience.audienceLoading}
            audienceError={liveAudience.audienceError}
            operationError={liveAudience.operationError}
            pendingViewerId={liveAudience.pendingViewerId}
            onRetryAudience={liveAudience.refresh}
            onDismissOperationError={liveAudience.clearOperationError}
            onMute={liveAudience.mute}
            onUnmute={liveAudience.unmute}
            onKick={liveAudience.kick}
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
            runtimeControl={{
              runtime: audienceRuntime.runtime,
              autoApply: audienceRuntime.autoApply,
              pending: audienceRuntime.pending,
              canApply: audienceRuntime.canApply,
              applying: audienceRuntime.applying,
              rollingBack: audienceRuntime.rollingBack,
              recovering: audienceRuntime.recovering,
              recoverableSessionId: backend.status?.recoverableRuntimeSessionId ?? null,
              canRecover:
                backend.connection === 'connected' &&
                Boolean(backend.status?.recoverableRuntimeSessionId),
              probing: audienceRuntime.probing,
              canProbe: backend.status?.providersConfigured ?? false,
              loadingTraces: audienceRuntime.loadingTraces,
              probe: audienceRuntime.probe,
              probeError: audienceRuntime.probeError,
              traces: audienceRuntime.traces,
              issue: audienceRuntime.issue,
              onAutoApplyChange: audienceRuntime.setAutoApply,
              onApply: () => void audienceRuntime.apply(),
              onRollback: () => void audienceRuntime.rollback(),
              onRecover: () => void audienceRuntime.recover(),
              onProbe: () => void audienceRuntime.runProbe(),
              onLoadTraces: () => void audienceRuntime.loadTraces()
            }}
            sharedBrain={sharedBrain}
            sharedBrainAvailable={
              backend.connection === 'connected' && Boolean(audienceRuntime.runtime)
            }
          />
        )}

        {activeView === 'settings' && (
          <SettingsView
            modelBaseUrl={modelConfig.baseUrl}
            providerProfileId={modelConfig.providerProfileId}
            modelName={modelConfig.model}
            viewerModel={modelConfig.viewerModel}
            memoryModel={modelConfig.memoryModel}
            visualSummaryModel={modelConfig.visualSummaryModel}
            apiKey={modelConfig.apiKey}
            asrApiKey={modelConfig.asrApiKey}
            modelConfigStatus={modelConfig.status}
            modelConfigLoading={modelConfig.loading}
            modelConfigSaving={modelConfig.saving}
            canSaveModelConfig={modelConfig.canSave}
            configNotice={modelConfig.notice}
            overlaySettings={overlay.settings}
            overlayTargets={overlay.targets}
            overlaySettingsNotice={overlay.notice}
            onModelBaseUrlChange={modelConfig.setBaseUrl}
            onProviderProfileIdChange={modelConfig.setProviderProfileId}
            onModelNameChange={modelConfig.setModel}
            onViewerModelChange={modelConfig.setViewerModel}
            onMemoryModelChange={modelConfig.setMemoryModel}
            onVisualSummaryModelChange={modelConfig.setVisualSummaryModel}
            onApiKeyChange={modelConfig.setApiKey}
            onAsrApiKeyChange={modelConfig.setAsrApiKey}
            onSaveModelConfig={() => void modelConfig.save()}
            onOverlaySettingsChange={overlay.updateSettings}
            onPreviewBarrage={(mode) => void barrage.previewBarrage(mode)}
          />
        )}

        {activeView === 'ai-calls' && (
          <AiCallLog
            active
            currentSessionId={backend.status?.session.sessionId ?? null}
          />
        )}
      </AppShell>

      {/* Keep media inputs and the frame canvas mounted while navigation swaps visible views. */}
      <div className="media-capture-sink" aria-hidden="true">
        <video ref={media.capturePipelineVideoRef} autoPlay muted playsInline />
        <video ref={media.cameraPipelineVideoRef} autoPlay muted playsInline />
        <canvas ref={visualPipeline.compositeCanvasRef} />
      </div>

      {media.sourcePickerOpen && (
        <SourcePickerDialog
          onClose={() => media.setSourcePickerOpen(false)}
          onSelect={media.chooseSource}
        />
      )}
    </>
  )
}
