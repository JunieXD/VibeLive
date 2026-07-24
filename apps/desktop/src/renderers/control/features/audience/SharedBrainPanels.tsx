import {
  Archive,
  Check,
  Pin,
  PinOff,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
  Undo2,
  X
} from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ModeMeme, RoomLongTermMemory } from '../../../../shared/backend-client'
import type { useSharedBrain } from '../../hooks/useSharedBrain'
import { cx } from './styles'

export type SharedBrainController = ReturnType<typeof useSharedBrain>

const memoryTypeLabels: Record<RoomLongTermMemory['memory_type'], string> = {
  user_preference: '用户偏好',
  real_world_fact: '现实事实',
  room_lore: '房间设定',
  shared_experience: '共同经历'
}

export function RoomMemoryPanel({
  brain,
  available
}: {
  brain: SharedBrainController
  available: boolean
}): React.JSX.Element {
  const [selectedId, setSelectedId] = useState('')
  const [draft, setDraft] = useState('')
  const selected = brain.memories.find((memory) => memory.memory_id === selectedId)
    ?? brain.memories[0]

  useEffect(() => {
    setDraft(selected?.content ?? '')
  }, [selected?.memory_id, selected?.content])

  if (!available) return <SharedBrainUnavailable />
  return (
    <div className={cx('aw-brain-layout')}>
      <aside className={cx('aw-brain-list')}>
        <div className={cx('aw-brain-heading')}>
          <strong>房间长期记忆</strong>
          <button type="button" title="刷新" disabled={brain.loading} onClick={() => void brain.refresh()}>
            <RefreshCw size={14} />
          </button>
        </div>
        {brain.memories.map((memory) => (
          <button
            type="button"
            key={memory.memory_id}
            className={cx('aw-brain-row', memory.memory_id === selected?.memory_id && 'selected')}
            onClick={() => setSelectedId(memory.memory_id)}
          >
            <span>{memoryTypeLabels[memory.memory_type]}</span>
            <strong>{memory.content}</strong>
            <small>r{memory.revision}{memory.revoked_at_ms ? ' · 已撤销' : ''}</small>
          </button>
        ))}
        {brain.memories.length === 0 && <p className={cx('aw-brain-empty')}>暂无长期记忆</p>}
      </aside>
      <section className={cx('aw-brain-editor')}>
        {brain.issue && <p className={cx('aw-inline-error')}>{brain.issue}</p>}
        {selected ? (
          <>
            <label>
              <span>记忆内容</span>
              <textarea value={draft} onChange={(event) => setDraft(event.target.value)} />
            </label>
            <div className={cx('aw-brain-actions')}>
              <button
                type="button"
                disabled={brain.busy || !draft.trim()}
                onClick={() => void brain.editMemory(selected, draft.trim())}
              >
                <Save size={14} />保存
              </button>
              {!selected.revoked_at_ms && (
                <button type="button" disabled={brain.busy} onClick={() => void brain.revokeMemory(selected)}>
                  <Undo2 size={14} />撤销
                </button>
              )}
              <button className={cx('danger')} type="button" disabled={brain.busy} onClick={() => void brain.deleteMemory(selected)}>
                <Trash2 size={14} />删除
              </button>
            </div>
          </>
        ) : (
          <p className={cx('aw-brain-empty')}>选择一条记忆进行编辑</p>
        )}
        <button
          className={cx('danger')}
          type="button"
          disabled={brain.busy || !brain.memoryHead || brain.memories.length === 0}
          onClick={() => void brain.resetMemories()}
        >
          <RotateCcw size={14} />重置房间记忆
        </button>
      </section>
    </div>
  )
}

export function BackendMemePanel({
  brain,
  available
}: {
  brain: SharedBrainController
  available: boolean
}): React.JSX.Element {
  const [selectedId, setSelectedId] = useState('')
  const [draft, setDraft] = useState('')
  const selected = brain.memes.find((meme) => meme.meme_id === selectedId) ?? brain.memes[0]

  useEffect(() => {
    setDraft(selected?.text ?? '')
  }, [selected?.meme_id, selected?.text])

  if (!available) return <SharedBrainUnavailable />
  return (
    <div className={cx('aw-brain-layout')}>
      <aside className={cx('aw-brain-list')}>
        <div className={cx('aw-brain-heading')}>
          <strong>后端梗库</strong>
          <label className={cx('aw-switch')} title="自动收录候选梗">
            <input
              type="checkbox"
              checked={brain.autoIngest?.enabled ?? false}
              disabled={!brain.autoIngest || brain.busy}
              onChange={(event) => void brain.setAutoIngest(event.target.checked)}
            />
            <span aria-hidden="true" />
          </label>
        </div>
        {brain.memes.map((meme) => (
          <button
            type="button"
            key={meme.meme_id}
            className={cx('aw-brain-row', meme.meme_id === selected?.meme_id && 'selected')}
            onClick={() => setSelectedId(meme.meme_id)}
          >
            <span>{meme.state}{meme.pinned ? ' · 置顶' : ''}</span>
            <strong>{meme.text}</strong>
            <small>使用 {meme.use_count} · r{meme.revision}</small>
          </button>
        ))}
        {brain.memes.length === 0 && <p className={cx('aw-brain-empty')}>后端暂无梗条目</p>}
      </aside>
      <section className={cx('aw-brain-editor')}>
        {brain.issue && <p className={cx('aw-inline-error')}>{brain.issue}</p>}
        {selected && (
          <>
            <label>
              <span>梗文本</span>
              <textarea value={draft} onChange={(event) => setDraft(event.target.value)} />
            </label>
            <div className={cx('aw-brain-actions')}>
              <button type="button" disabled={brain.busy || !draft.trim()} onClick={() => void brain.editMeme(selected, draft.trim())}>
                <Save size={14} />保存
              </button>
              <MemeAction brain={brain} meme={selected} action="undo" label="撤销" icon={<Undo2 size={14} />} />
              {selected.state === 'active' ? (
                <MemeAction brain={brain} meme={selected} action="disable" label="停用" icon={<X size={14} />} />
              ) : (
                <MemeAction brain={brain} meme={selected} action="restore" label="恢复" icon={<RotateCcw size={14} />} />
              )}
              <MemeAction
                brain={brain}
                meme={selected}
                action={selected.pinned ? 'unpin' : 'pin'}
                label={selected.pinned ? '取消置顶' : '置顶'}
                icon={selected.pinned ? <PinOff size={14} /> : <Pin size={14} />}
              />
              <MemeAction brain={brain} meme={selected} action="archive" label="归档" icon={<Archive size={14} />} />
            </div>
          </>
        )}
        <div className={cx('aw-brain-candidates')}>
          <strong>待审核候选 ({brain.candidates.length})</strong>
          {brain.candidates.map((candidate) => (
            <div key={candidate.candidate_id}>
              <span>{candidate.text}</span>
              <button type="button" title="通过" disabled={brain.busy} onClick={() => void brain.decideCandidate(candidate, 'approve')}>
                <Check size={14} />
              </button>
              <button type="button" title="拒绝" disabled={brain.busy} onClick={() => void brain.decideCandidate(candidate, 'reject')}>
                <X size={14} />
              </button>
            </div>
          ))}
          {brain.candidates.length === 0 && <p className={cx('aw-brain-empty')}>没有待审核候选</p>}
        </div>
      </section>
    </div>
  )
}

function MemeAction({
  brain,
  meme,
  action,
  label,
  icon
}: {
  brain: SharedBrainController
  meme: ModeMeme
  action: Parameters<SharedBrainController['mutateMeme']>[1]
  label: string
  icon: React.ReactNode
}): React.JSX.Element {
  return (
    <button type="button" disabled={brain.busy} onClick={() => void brain.mutateMeme(meme, action)}>
      {icon}{label}
    </button>
  )
}

function SharedBrainUnavailable(): React.JSX.Element {
  return (
    <div className={cx('aw-brain-unavailable')}>
      开始直播并连接后端后，可管理当前房间的长期记忆与梗库。
    </div>
  )
}
