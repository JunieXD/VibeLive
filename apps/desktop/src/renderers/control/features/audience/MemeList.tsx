import { Pin, Plus, Search } from 'lucide-react'
import type { MemeEntry, MemeStatus } from '../../../../shared/audience'
import { IconButton } from './IconButton'
import { cx } from './styles'

export type MemeFilter = 'all' | MemeStatus | 'new'

type MemeListProps = {
  entries: readonly MemeEntry[]
  selectedMemeId: string | undefined
  search: string
  filter: MemeFilter
  stats: { active: number; new: number; archived: number }
  sourceLabels: Record<MemeEntry['sourceKinds'][number], string>
  statusLabels: Record<MemeStatus, string>
  onSearchChange(value: string): void
  onFilterChange(filter: MemeFilter): void
  onAdd(): void
  onSelect(memeId: string): void
}

export function MemeList({
  entries,
  selectedMemeId,
  search,
  filter,
  stats,
  sourceLabels,
  statusLabels,
  onSearchChange,
  onFilterChange,
  onAdd,
  onSelect
}: MemeListProps): React.JSX.Element {
  return (
    <aside className={cx('aw-meme-list-pane')}>
      <div className={cx('aw-meme-stats')}>
        <span>
          <b>{stats.active}</b> 启用
        </span>
        <span>
          <b>{stats.new}</b> 新梗
        </span>
        <span>
          <b>{stats.archived}</b> 归档
        </span>
      </div>
      <div className={cx('aw-search-row')}>
        <Search size={14} />
        <input
          value={search}
          placeholder="搜索文本、家族或人格"
          onChange={(event) => onSearchChange(event.target.value)}
        />
        <select
          value={filter}
          aria-label="梗状态筛选"
          onChange={(event) => onFilterChange(event.target.value as MemeFilter)}
        >
          <option value="all">全部</option>
          <option value="active">启用</option>
          <option value="new">新梗</option>
          <option value="inactive">停用</option>
          <option value="archived">归档</option>
        </select>
        <IconButton title="手动新增梗" onClick={onAdd}>
          <Plus size={15} />
        </IconButton>
      </div>
      <div className={cx('aw-meme-list')}>
        {entries.map((entry) => (
          <button
            type="button"
            key={entry.id}
            className={cx('aw-meme-row', entry.id === selectedMemeId && 'selected')}
            onClick={() => onSelect(entry.id)}
          >
            <span className={cx('aw-meme-row-title')}>
              {entry.pinned && <Pin size={11} />}
              <strong>{entry.text}</strong>
            </span>
            <span className={cx('aw-tags')}>
              <i className={cx(entry.source)}>
                {entry.source === 'automatic' ? '导演' : '用户'}
              </i>
              {entry.sourceKinds.map((kind) => (
                <i key={kind}>{sourceLabels[kind]}</i>
              ))}
              <i>{statusLabels[entry.status]}</i>
            </span>
          </button>
        ))}
        {entries.length === 0 && <div className={cx('aw-empty')}>当前筛选下没有梗</div>}
      </div>
    </aside>
  )
}
