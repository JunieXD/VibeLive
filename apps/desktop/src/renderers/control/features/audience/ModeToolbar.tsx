import { Copy, RotateCcw, SlidersHorizontal, Trash2 } from 'lucide-react'
import {
  totalViewerCount,
  type AudienceMode,
  type AudienceWorkspaceState
} from '../../../../shared/audience'
import { SelectDropdown } from '../../components/SelectDropdown'
import { IconButton } from './IconButton'
import { Popover } from './Popover'
import { cx } from './styles'

type ModeToolbarProps = {
  workspace: AudienceWorkspaceState
  activeMode: AudienceMode
  structureLocked: boolean
  modeNameDraft: string
  onModeNameDraftChange(value: string): void
  onModeNameCommit(): void
  onSelectMode(modeId: string): void
  onPatchMode(change: Partial<AudienceMode>): void
  onDuplicateMode(): void
  onResetMode(): void
  onDeleteMode(): void
}

function clampNumber(value: string, minimum: number, maximum: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? Math.min(maximum, Math.max(minimum, Math.round(parsed)))
    : minimum
}

export function visualSettingsForBarrageGenerationMode(
  current: AudienceMode['visualSettings'],
  barrageGenerationMode: AudienceMode['visualSettings']['barrageGenerationMode']
): AudienceMode['visualSettings'] {
  if (barrageGenerationMode === 'per_viewer') {
    return { ...current, barrageGenerationMode }
  }
  return {
    ...current,
    barrageGenerationMode,
    viewerVisualInputMode: 'direct_frames',
    frameBundleSize: 4,
    frameWindowMs: 30_000,
    frameSelectionStrategy: 'change_peaks',
    frameMaxDimension: 768,
    frameQuality: 0.7
  }
}

export function ModeToolbar({
  workspace,
  activeMode,
  structureLocked,
  modeNameDraft,
  onModeNameDraftChange,
  onModeNameCommit,
  onSelectMode,
  onPatchMode,
  onDuplicateMode,
  onResetMode,
  onDeleteMode
}: ModeToolbarProps): React.JSX.Element {
  const windowBatch = activeMode.visualSettings.barrageGenerationMode === 'window_batch'
  const viewerCount = totalViewerCount(activeMode)
  const dispatch = activeMode.dispatchSettings
  const patchDispatchSettings = (
    change: Partial<AudienceMode['dispatchSettings']>
  ): void => {
    onPatchMode({ dispatchSettings: { ...dispatch, ...change } })
  }
  return (
    <header className={cx('aw-mode-toolbar')}>
      <div className={cx('aw-mode-main')}>
        <label>
          <span>观众模式</span>
          <SelectDropdown
            ariaLabel="观众模式"
            value={activeMode.id}
            disabled={structureLocked}
            options={workspace.modeState.modes.map((mode) => ({
              value: mode.id,
              label: mode.name
            }))}
            onChange={onSelectMode}
          />
        </label>
        <div className={cx('aw-mode-copy')} data-audience-mode-copy>
          {activeMode.builtIn ? (
            <strong>{activeMode.name}</strong>
          ) : (
            <input
              className={cx('aw-mode-name-input')}
              aria-label="自定义模式名称"
              value={modeNameDraft}
              disabled={structureLocked}
              onChange={(event) => onModeNameDraftChange(event.target.value)}
              onBlur={onModeNameCommit}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur()
                if (event.key === 'Escape') {
                  onModeNameDraftChange(activeMode.name)
                  event.currentTarget.blur()
                }
              }}
            />
          )}
          <span>{activeMode.description}</span>
        </div>
      </div>
      <div className={cx('aw-mode-controls')}>
        <span
          className={cx('aw-mode-summary')}
          title="在线人数 · 文本/语音每波 · 画面每波 · 系统音频/静默每波"
        >
          在线 {viewerCount} · 文本 {dispatch.userSpeakerBudget} · 画面{' '}
          {dispatch.screenSpeakerBudget} · 静默 {dispatch.ambientSpeakerBudget}
        </span>
        <Popover
          title="模式参数"
          trigger={
            <>
              <SlidersHorizontal size={15} />
              <span>参数</span>
            </>
          }
        >
          <div className={cx('aw-popover-section')}>
            <h4>发言调度</h4>
            <label>
              <span>文本/语音每波</span>
              <input
                type="number"
                min={0}
                max={32}
                value={dispatch.userSpeakerBudget}
                onChange={(event) => patchDispatchSettings({
                  userSpeakerBudget: clampNumber(event.target.value, 0, 32)
                })}
              />
            </label>
            <label>
              <span>画面变化每波</span>
              <input
                type="number"
                min={0}
                max={32}
                value={dispatch.screenSpeakerBudget}
                onChange={(event) => patchDispatchSettings({
                  screenSpeakerBudget: clampNumber(event.target.value, 0, 32)
                })}
              />
            </label>
            <label>
              <span>系统音频/静默每波</span>
              <input
                type="number"
                min={0}
                max={32}
                value={dispatch.ambientSpeakerBudget}
                onChange={(event) => patchDispatchSettings({
                  ambientSpeakerBudget: clampNumber(event.target.value, 0, 32)
                })}
              />
            </label>
          </div>
          <div className={cx('aw-popover-section')}>
            <h4>运行限流</h4>
            <label>
              <span>并发请求</span>
              <input
                type="number"
                min={1}
                max={32}
                value={dispatch.maxInFlightViewerRequests}
                onChange={(event) => patchDispatchSettings({
                  maxInFlightViewerRequests: clampNumber(event.target.value, 1, 32)
                })}
              />
            </label>
            <label>
              <span>队列容量</span>
              <input
                type="number"
                min={1}
                max={65_536}
                value={dispatch.viewerQueueCapacity}
                onChange={(event) => patchDispatchSettings({
                  viewerQueueCapacity: clampNumber(event.target.value, 1, 65_536)
                })}
              />
            </label>
            <label>
              <span>静默间隔 ms</span>
              <input
                type="number"
                min={1}
                max={Number.MAX_SAFE_INTEGER}
                step={1000}
                value={dispatch.ambientTickCooldownMs}
                onChange={(event) => patchDispatchSettings({
                  ambientTickCooldownMs: clampNumber(
                    event.target.value,
                    1,
                    Number.MAX_SAFE_INTEGER
                  )
                })}
              />
            </label>
            <label>
              <span>连续静默波数</span>
              <input
                type="number"
                min={0}
                max={32}
                value={dispatch.maxConsecutiveAmbientWaves}
                onChange={(event) => patchDispatchSettings({
                  maxConsecutiveAmbientWaves: clampNumber(event.target.value, 0, 32)
                })}
              />
            </label>
          </div>
          <div className={cx('aw-popover-section')}>
            <h4>行为策略</h4>
            <label>
              <span>算法模式</span>
              <SelectDropdown
                ariaLabel="算法模式"
                compact
                value={activeMode.visualSettings.barrageGenerationMode}
                options={[
                  { value: 'per_viewer', label: '逐观众生成' },
                  { value: 'window_batch', label: '30 秒窗口聚合' }
                ]}
                onChange={(barrageGenerationMode) =>
                  onPatchMode({
                    visualSettings: visualSettingsForBarrageGenerationMode(
                      activeMode.visualSettings,
                      barrageGenerationMode
                    )
                  })
                }
              />
            </label>
            <label>
              <span>冷场策略</span>
              <SelectDropdown
                ariaLabel="冷场策略"
                compact
                value={activeMode.ambience}
                options={[
                  { value: 'natural', label: '自然静默' },
                  { value: 'continuous', label: '持续暖场' }
                ]}
                onChange={(ambience) => onPatchMode({ ambience })}
              />
            </label>
            <label>
              <span>视觉输入</span>
              <SelectDropdown
                ariaLabel="视觉输入"
                compact
                disabled={windowBatch}
                value={activeMode.visualSettings.viewerVisualInputMode}
                options={[
                  { value: 'text_only', label: '纯文本' },
                  { value: 'direct_frames', label: '独立帧' },
                  { value: 'shared_summary', label: '共享摘要' }
                ]}
                onChange={(viewerVisualInputMode) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      viewerVisualInputMode
                    }
                  })
                }
              />
            </label>
          </div>
          <div className={cx('aw-popover-section')}>
            <h4>帧束策略</h4>
            <label>
              <span>选择策略</span>
              <SelectDropdown
                ariaLabel="帧选择策略"
                compact
                disabled={windowBatch}
                value={activeMode.visualSettings.frameSelectionStrategy}
                options={[
                  { value: 'change_peaks', label: '相似去重' },
                  { value: 'latest_n', label: '最新帧' }
                ]}
                onChange={(frameSelectionStrategy) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameSelectionStrategy
                    }
                  })
                }
              />
            </label>
            <label>
              <span>帧数</span>
              <input
                type="number"
                min={1}
                max={5}
                disabled={windowBatch}
                value={activeMode.visualSettings.frameBundleSize}
                onChange={(event) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameBundleSize: clampNumber(
                        event.target.value,
                        1,
                        5
                      )
                    }
                  })
                }
              />
            </label>
            <label>
              <span>窗口 ms</span>
              <input
                type="number"
                min={1}
                max={30_000}
                step={500}
                disabled={windowBatch}
                value={activeMode.visualSettings.frameWindowMs}
                onChange={(event) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameWindowMs: clampNumber(
                        event.target.value,
                        1,
                        30_000
                      )
                    }
                  })
                }
              />
            </label>
            <label>
              <span>最长边</span>
              <input
                type="number"
                min={64}
                max={8192}
                step={64}
                value={activeMode.visualSettings.frameMaxDimension}
                onChange={(event) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameMaxDimension: clampNumber(event.target.value, 64, 8192)
                    }
                  })
                }
              />
            </label>
            <label>
              <span>质量 %</span>
              <input
                type="number"
                min={1}
                max={100}
                value={Math.round(activeMode.visualSettings.frameQuality * 100)}
                onChange={(event) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameQuality: clampNumber(event.target.value, 1, 100) / 100
                    }
                  })
                }
              />
            </label>
          </div>
        </Popover>
        <div className={cx('aw-toolbar-actions')}>
          <IconButton
            title="复制为自定义模式"
            disabled={structureLocked}
            onClick={onDuplicateMode}
          >
            <Copy size={15} />
          </IconButton>
          {activeMode.builtIn ? (
            <IconButton title="重置内置模式" disabled={structureLocked} onClick={onResetMode}>
              <RotateCcw size={15} />
            </IconButton>
          ) : (
            <IconButton
              title="删除自定义模式"
              danger
              disabled={structureLocked}
              onClick={onDeleteMode}
            >
              <Trash2 size={15} />
            </IconButton>
          )}
        </div>
      </div>
    </header>
  )
}
