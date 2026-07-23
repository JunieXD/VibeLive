import { Copy, RotateCcw, Trash2 } from 'lucide-react'
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

function clampActivityValue(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.min(99, Math.max(0, Math.trunc(parsed))) : 0
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
        <label className={cx('aw-range-control')} data-audience-range>
          <span>导演活跃目标</span>
          <input
            type="number"
            min={0}
            max={99}
            value={activeMode.baseActivity[0]}
            onChange={(event) =>
              onPatchMode({
                baseActivity: [
                  Math.min(clampActivityValue(event.target.value), activeMode.baseActivity[1]),
                  activeMode.baseActivity[1]
                ]
              })
            }
          />
          <b>至</b>
          <input
            type="number"
            min={0}
            max={99}
            value={activeMode.baseActivity[1]}
            onChange={(event) =>
              onPatchMode({
                baseActivity: [
                  activeMode.baseActivity[0],
                  Math.max(clampActivityValue(event.target.value), activeMode.baseActivity[0])
                ]
              })
            }
          />
        </label>
        <label className={cx('aw-range-control')} data-audience-range>
          <span>导演爆点目标</span>
          <input
            type="number"
            min={0}
            max={99}
            value={activeMode.burstLimit[0]}
            onChange={(event) =>
              onPatchMode({
                burstLimit: [
                  Math.min(clampActivityValue(event.target.value), activeMode.burstLimit[1]),
                  activeMode.burstLimit[1]
                ]
              })
            }
          />
          <b>至</b>
          <input
            type="number"
            min={0}
            max={99}
            value={activeMode.burstLimit[1]}
            onChange={(event) =>
              onPatchMode({
                burstLimit: [
                  activeMode.burstLimit[0],
                  Math.max(clampActivityValue(event.target.value), activeMode.burstLimit[0])
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
