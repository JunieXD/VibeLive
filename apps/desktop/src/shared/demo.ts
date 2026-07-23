export type AudienceMember = {
  readonly id: string
  readonly name: string
  readonly initials: string
  readonly role: string
  readonly color: string
  active: boolean
  readonly memory: string
}

export const initialAudience: AudienceMember[] = [
  {
    id: 'lin',
    name: '林澈',
    initials: 'LC',
    role: '细节观察',
    color: '#177b64',
    active: true,
    memory: '关注画面里的操作细节，回复克制。'
  },
  {
    id: 'mia',
    name: '米娅',
    initials: 'MY',
    role: '气氛回应',
    color: '#ce5b3f',
    active: true,
    memory: '更容易注意到情绪变化和现场节奏。'
  },
  {
    id: 'kepler',
    name: '开普勒',
    initials: 'KP',
    role: '理性提问',
    color: '#3c68b1',
    active: true,
    memory: '喜欢追问原因，不轻易下结论。'
  },
  {
    id: 'qiu',
    name: '秋原',
    initials: 'QY',
    role: '安静陪伴',
    color: '#9b7422',
    active: false,
    memory: '发言频率低，通常只回应明确点名。'
  }
]

export const demoLines = [
  '这个窗口里的变化挺明显，我先记住这一段。',
  '刚才的操作节奏比前面快了一些。',
  '我有点好奇，你下一步准备切到哪里？',
  '这段画面信息很多，但重点应该在左上角。',
  '收到，我会继续看着当前话题。',
  '这里先别急着下结论，再观察一轮。'
]
