import { Archive, ArchiveRestore, Pin, PinOff, Save, Trash2, Undo2 } from 'lucide-react'
import type { Dispatch, SetStateAction } from 'react'
import type { MemeEntry } from '../../../../shared/audience'
import {
  archiveMeme,
  disableMeme,
  setMemePinned,
  undoAutomaticMeme
} from '../../../../shared/audience'
import { IconButton } from './IconButton'
import { cx } from './styles'

export type MemeDraft = {
  text: string
  familyKey: string
  personaTags: string
  evidenceSummary: string
}

type MemeEditorProps = {
  memes: readonly MemeEntry[]
  selectedMeme: MemeEntry | undefined
  draft: MemeDraft | null
  error: string
  sourceLabels: Record<MemeEntry['sourceKinds'][number], string>
  setDraft: Dispatch<SetStateAction<MemeDraft | null>>
  onMutate(memes: readonly MemeEntry[]): void
  onRestore(): void
  onSave(): void
}

export function MemeEditor({
  memes,
  selectedMeme,
  draft,
  error,
  sourceLabels,
  setDraft,
  onMutate,
  onRestore,
  onSave
}: MemeEditorProps): React.JSX.Element {
  if (!selectedMeme || !draft) {
    return (
      <main className={cx('aw-meme-editor')}>
        <div className={cx('aw-empty')}>选择或新增一个梗</div>
      </main>
    )
  }

  const update = (change: Partial<MemeDraft>): void => setDraft({ ...draft, ...change })

  return (
    <main className={cx('aw-meme-editor')}>
      <div className={cx('aw-editor-heading')}>
        <div>
          <strong>{selectedMeme.source === 'automatic' ? '导演自动梗' : '手动梗'}</strong>
          <span>
            {selectedMeme.sourceKinds.map((kind) => sourceLabels[kind]).join(' · ')}
            {' · '}使用 {selectedMeme.usageCount} 次
          </span>
        </div>
        <div className={cx('aw-editor-actions')}>
          <IconButton
            title={selectedMeme.pinned ? '取消置顶' : '置顶'}
            disabled={selectedMeme.status === 'archived'}
            onClick={() =>
              onMutate(setMemePinned(memes, selectedMeme.id, !selectedMeme.pinned))
            }
          >
            {selectedMeme.pinned ? <PinOff size={15} /> : <Pin size={15} />}
          </IconButton>
          {selectedMeme.source === 'automatic' && (
            <IconButton
              title="撤销自动梗"
              onClick={() => onMutate(undoAutomaticMeme(memes, selectedMeme.id))}
            >
              <Undo2 size={15} />
            </IconButton>
          )}
          {selectedMeme.status === 'archived' ? (
            <IconButton title="恢复梗" onClick={onRestore}>
              <ArchiveRestore size={15} />
            </IconButton>
          ) : (
            <IconButton
              title="归档梗"
              onClick={() => onMutate(archiveMeme(memes, selectedMeme.id))}
            >
              <Archive size={15} />
            </IconButton>
          )}
          <IconButton
            title="删除梗"
            danger
            onClick={() => onMutate(memes.filter((entry) => entry.id !== selectedMeme.id))}
          >
            <Trash2 size={15} />
          </IconButton>
          <button type="button" className={cx('aw-save-button')} onClick={onSave}>
            <Save size={15} />
            保存
          </button>
        </div>
      </div>
      <div className={cx('aw-meme-meta')}>
        <span>{selectedMeme.createdBy === 'director' ? '导演生成' : '用户创建'}</span>
        <span>{new Date(selectedMeme.createdAt).toLocaleString()}</span>
        <span>rev {selectedMeme.revision}</span>
        <label className={cx('aw-switch')}>
          <input
            type="checkbox"
            checked={selectedMeme.status === 'active'}
            disabled={selectedMeme.status === 'archived'}
            onChange={() =>
              selectedMeme.status === 'active'
                ? onMutate(disableMeme(memes, selectedMeme.id))
                : onRestore()
            }
          />
          <span aria-hidden="true" />
          <em>{selectedMeme.status === 'active' ? '启用' : '停用'}</em>
        </label>
      </div>
      <div className={cx('aw-form', 'aw-meme-form')} data-audience-meme-form>
        {error && <div className={cx('aw-validation')}>{error}</div>}
        <label>
          <span>弹幕文本</span>
          <textarea value={draft.text} onChange={(event) => update({ text: event.target.value })} />
        </label>
        <div className={cx('aw-form-grid', 'two')}>
          <label>
            <span>梗家族</span>
            <input
              value={draft.familyKey}
              onChange={(event) => update({ familyKey: event.target.value })}
            />
          </label>
          <label>
            <span>人格标签</span>
            <input
              value={draft.personaTags}
              onChange={(event) => update({ personaTags: event.target.value })}
            />
          </label>
        </div>
        <label>
          <span>证据摘要（最多 160 字）</span>
          <textarea
            maxLength={160}
            value={draft.evidenceSummary}
            onChange={(event) => update({ evidenceSummary: event.target.value })}
          />
        </label>
      </div>
    </main>
  )
}
