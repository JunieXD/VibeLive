import { Copy, RotateCcw, SlidersHorizontal, Trash2 } from 'lucide-react'
import type { AudienceMode, AudienceWorkspaceState } from '../../../../shared/audience'
import { IconButton } from './IconButton'
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

function clampActivityValue(value: string, maximum = 32): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(0, Math.trunc(parsed))) : 0
}

function clampNumber(value: string, minimum: number, maximum: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? Math.min(maximum, Math.max(minimum, Math.round(parsed)))
    : minimum
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
  return (
    <header className={cx('aw-mode-toolbar')}>
      <div className={cx('aw-mode-main')}>
        <label>
          <span>观众模式</span>
          <select
            value={activeMode.id}
            disabled={structureLocked}
            onChange={(event) => onSelectMode(event.target.value)}
          >
            {workspace.modeState.modes.map((mode) => (
              <option key={mode.id} value={mode.id}>
                {mode.name}
              </option>
            ))}
          </select>
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
        <label>
          <span>目标在线</span>
          <input
            type="number"
            min={1}
            max={32}
            value={activeMode.targetConcurrentViewers}
            onChange={(event) => {
              const targetConcurrentViewers = Math.max(1, clampActivityValue(event.target.value))
              onPatchMode({
                targetConcurrentViewers,
                normalResponseRange: [
                  Math.min(activeMode.normalResponseRange[0], targetConcurrentViewers),
                  Math.min(activeMode.normalResponseRange[1], targetConcurrentViewers)
                ],
                highlightResponseRange: [
                  Math.min(activeMode.highlightResponseRange[0], targetConcurrentViewers),
                  Math.min(activeMode.highlightResponseRange[1], targetConcurrentViewers)
                ]
              })
            }}
          />
        </label>
        <label className={cx('aw-range-control')} data-audience-range>
          <span>普通响应</span>
          <input
            type="number"
            min={0}
            max={activeMode.targetConcurrentViewers}
            value={activeMode.normalResponseRange[0]}
            onChange={(event) =>
              onPatchMode({
                normalResponseRange: [
                  Math.min(
                    clampActivityValue(event.target.value, activeMode.targetConcurrentViewers),
                    activeMode.normalResponseRange[1]
                  ),
                  activeMode.normalResponseRange[1]
                ]
              })
            }
          />
          <b>至</b>
          <input
            type="number"
            min={0}
            max={activeMode.targetConcurrentViewers}
            value={activeMode.normalResponseRange[1]}
            onChange={(event) =>
              onPatchMode({
                normalResponseRange: [
                  activeMode.normalResponseRange[0],
                  Math.max(
                    clampActivityValue(event.target.value, activeMode.targetConcurrentViewers),
                    activeMode.normalResponseRange[0]
                  )
                ]
              })
            }
          />
        </label>
        <label className={cx('aw-range-control')} data-audience-range>
          <span>高光响应</span>
          <input
            type="number"
            min={0}
            max={activeMode.targetConcurrentViewers}
            value={activeMode.highlightResponseRange[0]}
            onChange={(event) =>
              onPatchMode({
                highlightResponseRange: [
                  Math.min(
                    clampActivityValue(event.target.value, activeMode.targetConcurrentViewers),
                    activeMode.highlightResponseRange[1]
                  ),
                  activeMode.highlightResponseRange[1]
                ]
              })
            }
          />
          <b>至</b>
          <input
            type="number"
            min={0}
            max={activeMode.targetConcurrentViewers}
            value={activeMode.highlightResponseRange[1]}
            onChange={(event) =>
              onPatchMode({
                highlightResponseRange: [
                  activeMode.highlightResponseRange[0],
                  Math.max(
                    clampActivityValue(event.target.value, activeMode.targetConcurrentViewers),
                    activeMode.highlightResponseRange[0]
                  )
                ]
              })
            }
          />
        </label>
        <label>
          <span>冷场策略</span>
          <select
            value={activeMode.ambience}
            onChange={(event) =>
              onPatchMode({ ambience: event.target.value as AudienceMode['ambience'] })
            }
          >
            <option value="natural">自然静默</option>
            <option value="continuous">持续暖场</option>
          </select>
        </label>
        <label>
          <span>视觉</span>
          <select
            value={activeMode.visualSettings.viewerVisualInputMode}
            onChange={(event) =>
              onPatchMode({
                visualSettings: {
                  ...activeMode.visualSettings,
                  viewerVisualInputMode: event.target.value as
                    AudienceMode['visualSettings']['viewerVisualInputMode']
                }
              })
            }
          >
            <option value="text_only">纯文本</option>
            <option value="direct_frames">独立帧</option>
            <option value="shared_summary">共享摘要</option>
          </select>
        </label>
        <details className={cx('aw-frame-policy')}>
          <summary title="帧束策略">
            <SlidersHorizontal size={15} />
            <span>帧束</span>
          </summary>
          <div className={cx('aw-frame-policy-menu')}>
            <label>
              <span>选择策略</span>
              <select
                value={activeMode.visualSettings.frameSelectionStrategy}
                onChange={(event) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameSelectionStrategy: event.target.value as
                        AudienceMode['visualSettings']['frameSelectionStrategy']
                    }
                  })
                }
              >
                <option value="change_peaks">变化峰值</option>
                <option value="latest_n">最新帧</option>
                <option value="evenly_spaced">均匀采样</option>
              </select>
            </label>
            <label>
              <span>帧数</span>
              <input
                type="number"
                min={1}
                max={16}
                value={activeMode.visualSettings.frameBundleSize}
                onChange={(event) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameBundleSize: clampNumber(event.target.value, 1, 16)
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
                max={300000}
                step={500}
                value={activeMode.visualSettings.frameWindowMs}
                onChange={(event) =>
                  onPatchMode({
                    visualSettings: {
                      ...activeMode.visualSettings,
                      frameWindowMs: clampNumber(event.target.value, 1, 300_000)
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
        </details>
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
