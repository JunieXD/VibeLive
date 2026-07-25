import {
  ArrowLeft,
  KeyRound,
  MessageSquareText,
  PanelBottom,
  PanelTop,
  SlidersHorizontal,
  Sparkles
} from 'lucide-react'
import type {
  BarrageDisplayMode,
  BarrageMode,
  ModelConfigStatus,
  OverlaySettings,
  OverlayTarget
} from '../../../../shared/contracts'
import { SelectDropdown } from '../../components/SelectDropdown'

export type SettingsViewProps = {
  modelBaseUrl: string
  providerProfileId: string
  modelName: string
  viewerModel: string
  memoryModel: string
  visualSummaryModel: string
  apiKey: string
  asrBaseUrl: string
  asrModel: string
  asrApiKey: string
  modelConfigStatus: ModelConfigStatus | null
  modelConfigLoading: boolean
  modelConfigSaving: boolean
  canSaveModelConfig: boolean
  configNotice: string | null
  overlaySettings: OverlaySettings | null
  overlayTargets: readonly OverlayTarget[]
  overlaySettingsNotice: string | null
  onModelBaseUrlChange: (value: string) => void
  onProviderProfileIdChange: (value: string) => void
  onModelNameChange: (value: string) => void
  onViewerModelChange: (value: string) => void
  onMemoryModelChange: (value: string) => void
  onVisualSummaryModelChange: (value: string) => void
  onApiKeyChange: (value: string) => void
  onAsrBaseUrlChange: (value: string) => void
  onAsrModelChange: (value: string) => void
  onAsrApiKeyChange: (value: string) => void
  onSaveModelConfig: () => void
  onOverlaySettingsChange: (settings: OverlaySettings) => void
  onPreviewBarrage: (mode: BarrageMode) => void
}

const surfaceClassName =
  'min-w-0 rounded-lg border border-[var(--border)] bg-[var(--panel)] p-5 text-[var(--text)]'
const labelClassName = 'grid gap-2 text-xs font-semibold text-[var(--text-dim)]'
const controlClassName =
  'min-h-9 w-full rounded-lg border border-[var(--border-strong)] bg-[var(--bg)] px-3 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50'
const actionButtonClassName =
  'inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-[var(--border-strong)] bg-[var(--panel-raise)] px-3 text-xs font-bold text-[var(--text)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]'
const sliderClassName = 'w-full cursor-pointer accent-[var(--accent)]'
const BARRAGE_DISPLAY_MODES: readonly BarrageDisplayMode[] = ['overlay', 'floating']

type SliderFieldProps = {
  label: string
  value: number
  suffix?: string
  min: number
  max: number
  step?: number
  onChange: (value: number) => void
}

function SliderField({
  label,
  value,
  suffix = '',
  min,
  max,
  step,
  onChange
}: SliderFieldProps): React.JSX.Element {
  return (
    <label className="grid gap-2">
      <span className="flex items-center justify-between gap-4 text-xs text-[var(--text-dim)]">
        {label}
        <strong className="font-mono text-xs text-[var(--text)]">
          {value}
          {suffix}
        </strong>
      </span>
      <input
        className={sliderClassName}
        aria-label={label}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  )
}

type ToggleFieldProps = {
  label: string
  checked: boolean
  disabled?: boolean
  notice?: string | null
  onChange: (checked: boolean) => void
}

function ToggleField({
  label,
  checked,
  disabled = false,
  notice,
  onChange
}: ToggleFieldProps): React.JSX.Element {
  return (
    <div className="flex min-h-11 items-center justify-between gap-4 border-t border-[var(--border)] py-2">
      <span className="grid gap-1 text-xs font-semibold text-[var(--text-dim)]">
        {label}
        {notice && (
          <small className="font-normal text-[var(--text-faint)]" role="status" aria-live="polite">
            {notice}
          </small>
        )}
      </span>
      <label
        className={`relative inline-flex items-center gap-2 ${
          disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'
        }`}
      >
        <input
          className="peer absolute inset-0 z-10 m-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
          aria-label={label}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span
          className="pointer-events-none relative h-5 w-9 rounded-full border border-[var(--border-strong)] bg-[var(--bg)] transition-colors after:absolute after:left-0.5 after:top-0.5 after:h-3.5 after:w-3.5 after:rounded-full after:bg-[var(--text-dim)] after:transition-transform after:content-[''] peer-checked:border-[var(--accent)] peer-checked:bg-[var(--accent-soft)] peer-checked:after:translate-x-4 peer-checked:after:bg-[var(--accent)] peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--accent)]"
          aria-hidden="true"
        />
        <em className="w-7 text-right text-[10px] not-italic text-[var(--text-faint)]">
          {checked ? '开启' : '关闭'}
        </em>
      </label>
    </div>
  )
}

export function SettingsView({
  modelBaseUrl,
  providerProfileId,
  modelName,
  viewerModel,
  memoryModel,
  visualSummaryModel,
  apiKey,
  asrBaseUrl,
  asrModel,
  asrApiKey,
  modelConfigStatus,
  modelConfigLoading,
  modelConfigSaving,
  canSaveModelConfig,
  configNotice,
  overlaySettings,
  overlayTargets,
  overlaySettingsNotice,
  onModelBaseUrlChange,
  onProviderProfileIdChange,
  onModelNameChange,
  onViewerModelChange,
  onMemoryModelChange,
  onVisualSummaryModelChange,
  onApiKeyChange,
  onAsrBaseUrlChange,
  onAsrModelChange,
  onAsrApiKeyChange,
  onSaveModelConfig,
  onOverlaySettingsChange,
  onPreviewBarrage
}: SettingsViewProps): React.JSX.Element {
  const credentialsStored =
    modelConfigStatus?.modelApiKeyStored === true && modelConfigStatus.asrApiKeyStored === true
  const screenBarrageEnabled = overlaySettings?.displayModes.includes('overlay') ?? false
  const floatingChatEnabled = overlaySettings?.displayModes.includes('floating') ?? false
  const onlyDisplayModeEnabled = overlaySettings?.displayModes.length === 1

  const updateDisplayMode = (displayMode: BarrageDisplayMode, enabled: boolean): void => {
    if (!overlaySettings) return

    const displayModes = BARRAGE_DISPLAY_MODES.filter((mode) =>
      mode === displayMode ? enabled : overlaySettings.displayModes.includes(mode)
    )
    if (displayModes.length === 0) return

    onOverlaySettingsChange({ ...overlaySettings, displayModes })
  }

  return (
    <div className="grid min-w-0 gap-4 xl:grid-cols-2">
      <section className={surfaceClassName}>
        <header className="mb-5 flex items-start justify-between gap-4">
          <div>
            <p className="mb-1 text-[10px] font-bold uppercase text-[var(--text-faint)]">模型连接</p>
            <h2 className="m-0 text-base font-semibold text-[var(--text)]">OpenAI-compatible</h2>
          </div>
          <Sparkles className="text-[var(--accent)]" size={24} aria-hidden="true" />
        </header>

        <div className="grid gap-4">
          <label className={labelClassName}>
            服务地址
            <input
              className={controlClassName}
              value={modelBaseUrl}
              onChange={(event) => onModelBaseUrlChange(event.target.value)}
            />
          </label>
          <label className={labelClassName}>
            模型名称
            <input
              className={controlClassName}
              value={modelName}
              onChange={(event) => onModelNameChange(event.target.value)}
              placeholder="输入多模态模型名称"
            />
          </label>
          <details className="rounded-lg border border-[var(--border)] p-3">
            <summary className="cursor-pointer text-xs font-semibold text-[var(--text-dim)]">
              高级模型覆盖
            </summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className={labelClassName}>
                供应商档案
                <input
                  className={controlClassName}
                  value={providerProfileId}
                  onChange={(event) => onProviderProfileIdChange(event.target.value)}
                  placeholder="default"
                />
              </label>
              <label className={labelClassName}>
                Viewer 模型
                <input
                  className={controlClassName}
                  value={viewerModel}
                  onChange={(event) => onViewerModelChange(event.target.value)}
                  placeholder={`继承 ${modelName || '默认模型'}`}
                />
              </label>
              <label className={labelClassName}>
                记忆模型
                <input
                  className={controlClassName}
                  value={memoryModel}
                  onChange={(event) => onMemoryModelChange(event.target.value)}
                  placeholder={`继承 ${modelName || '默认模型'}`}
                />
              </label>
              <label className={labelClassName}>
                视觉摘要模型
                <input
                  className={controlClassName}
                  value={visualSummaryModel}
                  onChange={(event) => onVisualSummaryModelChange(event.target.value)}
                  placeholder={`继承 ${modelName || '默认模型'}`}
                />
              </label>
            </div>
          </details>
          <label className={labelClassName}>
            <span className="flex items-center justify-between gap-3">
              模型 API Key
              {modelConfigStatus?.modelApiKeyStored && (
                <small className="font-normal text-[var(--accent)]">已安全保存</small>
              )}
            </span>
            <input
              className={controlClassName}
              type="password"
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
              placeholder={
                modelConfigStatus?.modelApiKeyStored
                  ? '••••••••（已保存，输入新值可替换）'
                  : '仅由 Electron Main 安全保存'
              }
            />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className={labelClassName}>
              ASR 服务地址
              <input
                className={controlClassName}
                value={asrBaseUrl}
                onChange={(event) => onAsrBaseUrlChange(event.target.value)}
              />
            </label>
            <label className={labelClassName}>
              ASR 模型
              <input
                className={controlClassName}
                value={asrModel}
                onChange={(event) => onAsrModelChange(event.target.value)}
              />
            </label>
          </div>
          <label className={labelClassName}>
            <span className="flex items-center justify-between gap-3">
              StepFun ASR API Key
              {modelConfigStatus?.asrApiKeyStored && (
                <small className="font-normal text-[var(--accent)]">已安全保存</small>
              )}
            </span>
            <input
              className={controlClassName}
              type="password"
              value={asrApiKey}
              onChange={(event) => onAsrApiKeyChange(event.target.value)}
              placeholder={
                modelConfigStatus?.asrApiKeyStored
                  ? '••••••••（已保存，输入新值可替换）'
                  : '用于实时语音识别'
              }
            />
          </label>
          <div className="flex min-h-9 flex-wrap items-center justify-end gap-3">
            {configNotice && (
              <span className="mr-auto text-xs text-[var(--text-dim)]" role="status">
                {configNotice}
              </span>
            )}
            <button
              className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-[var(--accent)] bg-[var(--accent)] px-4 text-xs font-bold text-[var(--accent-ink)] transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
              type="button"
              disabled={!canSaveModelConfig}
              onClick={onSaveModelConfig}
            >
              <KeyRound size={16} aria-hidden="true" />
              {modelConfigSaving
                ? '正在保存'
                : modelConfigLoading
                  ? '正在读取'
                  : credentialsStored
                    ? '保存更改'
                    : '保存连接'}
            </button>
          </div>
        </div>
      </section>

      <section className={surfaceClassName}>
        <header className="mb-5 flex items-start justify-between gap-4">
          <div>
            <p className="mb-1 text-[10px] font-bold uppercase text-[var(--text-faint)]">弹幕显示</p>
            <h2 className="m-0 text-base font-semibold text-[var(--text)]">弹幕窗口</h2>
          </div>
          <SlidersHorizontal className="text-[var(--accent)]" size={24} aria-hidden="true" />
        </header>

        {!overlaySettings ? (
          <div className="grid min-h-40 place-items-center rounded-lg border border-dashed border-[var(--border-strong)] text-sm text-[var(--text-dim)]">
            {overlaySettingsNotice ?? '正在读取覆盖层设置...'}
          </div>
        ) : (
          <div className="grid gap-4">
            <fieldset className="m-0 grid gap-2 border-0 p-0">
              <legend className="mb-2 text-xs font-semibold text-[var(--text-dim)]">
                显示方式
              </legend>
              <div className="grid gap-1" role="group" aria-label="弹幕显示方式">
                <ToggleField
                  label="屏幕弹幕"
                  checked={screenBarrageEnabled}
                  disabled={screenBarrageEnabled && onlyDisplayModeEnabled}
                  notice={
                    screenBarrageEnabled && onlyDisplayModeEnabled
                      ? '至少保留一种显示方式'
                      : null
                  }
                  onChange={(enabled) => updateDisplayMode('overlay', enabled)}
                />
                <ToggleField
                  label="互动悬浮窗"
                  checked={floatingChatEnabled}
                  disabled={floatingChatEnabled && onlyDisplayModeEnabled}
                  notice={
                    floatingChatEnabled && onlyDisplayModeEnabled
                      ? '至少保留一种显示方式'
                      : null
                  }
                  onChange={(enabled) => updateDisplayMode('floating', enabled)}
                />
              </div>
            </fieldset>

            {screenBarrageEnabled && (
              <>
                <div className={labelClassName}>
                  弹幕目标
                  <SelectDropdown
                    ariaLabel="弹幕目标"
                    triggerClassName={controlClassName}
                    value={overlaySettings.targetDisplayId}
                    options={overlayTargets.map((target) => ({
                      value: target.id,
                      label: `${target.isPrimary ? '主屏 · ' : ''}${target.name} · ${target.bounds.width} × ${target.bounds.height}`
                    }))}
                    onChange={(targetDisplayId) =>
                      onOverlaySettingsChange({
                        ...overlaySettings,
                        targetDisplayId
                      })
                    }
                  />
                </div>

                <div className={labelClassName}>
                  字体
                  <SelectDropdown
                    ariaLabel="弹幕字体"
                    triggerClassName={controlClassName}
                    value={overlaySettings.fontFamily}
                    options={[
                      { value: 'bilibili', label: 'B站默认' },
                      { value: 'yahei', label: '微软雅黑' },
                      { value: 'system', label: '系统字体' }
                    ]}
                    onChange={(fontFamily) =>
                      onOverlaySettingsChange({
                        ...overlaySettings,
                        fontFamily
                      })
                    }
                  />
                </div>

                <div className="grid gap-4 border-y border-[var(--border)] py-4">
                  <SliderField
                    label="字号"
                    value={overlaySettings.fontSizePx}
                    suffix="px"
                    min={14}
                    max={36}
                    onChange={(fontSizePx) =>
                      onOverlaySettingsChange({ ...overlaySettings, fontSizePx })
                    }
                  />
                  <SliderField
                    label="描边粗细"
                    value={overlaySettings.outlineWidthPx}
                    suffix="px"
                    min={0}
                    max={3}
                    step={0.5}
                    onChange={(outlineWidthPx) =>
                      onOverlaySettingsChange({ ...overlaySettings, outlineWidthPx })
                    }
                  />
                  <SliderField
                    label="移动速度"
                    value={overlaySettings.speed}
                    min={20}
                    max={100}
                    onChange={(speed) =>
                      onOverlaySettingsChange({ ...overlaySettings, speed })
                    }
                  />
                  <SliderField
                    label="透明度"
                    value={overlaySettings.opacity}
                    suffix="%"
                    min={30}
                    max={100}
                    onChange={(opacity) =>
                      onOverlaySettingsChange({ ...overlaySettings, opacity })
                    }
                  />
                  <SliderField
                    label="密度"
                    value={overlaySettings.density}
                    min={1}
                    max={100}
                    onChange={(density) =>
                      onOverlaySettingsChange({ ...overlaySettings, density })
                    }
                  />
                  <SliderField
                    label="显示区域顶部"
                    value={overlaySettings.region.topPercent}
                    suffix="%"
                    min={0}
                    max={overlaySettings.region.bottomPercent - 20}
                    onChange={(topPercent) =>
                      onOverlaySettingsChange({
                        ...overlaySettings,
                        region: { ...overlaySettings.region, topPercent }
                      })
                    }
                  />
                  <SliderField
                    label="显示区域底部"
                    value={overlaySettings.region.bottomPercent}
                    suffix="%"
                    min={overlaySettings.region.topPercent + 20}
                    max={100}
                    onChange={(bottomPercent) =>
                      onOverlaySettingsChange({
                        ...overlaySettings,
                        region: { ...overlaySettings.region, bottomPercent }
                      })
                    }
                  />
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span className="text-xs font-semibold text-[var(--text-dim)]">
                    弹幕预览
                  </span>
                  <div className="flex flex-wrap gap-2">
                    <button
                      className={actionButtonClassName}
                      type="button"
                      title="预览滚动弹幕"
                      onClick={() => onPreviewBarrage('scroll')}
                    >
                      <ArrowLeft size={15} aria-hidden="true" />
                      滚动
                    </button>
                    <button
                      className={actionButtonClassName}
                      type="button"
                      title="预览顶端固定弹幕"
                      onClick={() => onPreviewBarrage('top')}
                    >
                      <PanelTop size={15} aria-hidden="true" />
                      顶端
                    </button>
                    <button
                      className={actionButtonClassName}
                      type="button"
                      title="预览底端固定弹幕"
                      onClick={() => onPreviewBarrage('bottom')}
                    >
                      <PanelBottom size={15} aria-hidden="true" />
                      底端
                    </button>
                  </div>
                </div>

                <ToggleField
                  label="粗体"
                  checked={overlaySettings.bold}
                  onChange={(bold) =>
                    onOverlaySettingsChange({ ...overlaySettings, bold })
                  }
                />
              </>
            )}

            {floatingChatEnabled && (
              <div className="flex min-h-11 items-center justify-between gap-3 border-y border-[var(--border)] py-3">
                <span className="text-xs font-semibold text-[var(--text-dim)]">
                  互动窗预览
                </span>
                <button
                  className={actionButtonClassName}
                  type="button"
                  title="打开互动悬浮窗预览"
                  onClick={() => onPreviewBarrage('scroll')}
                >
                  <MessageSquareText size={15} aria-hidden="true" />
                  打开预览
                </button>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
