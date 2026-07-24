import { createPersonaTemplate } from './canonical'
import type { AudienceMode, AudienceVisualSettings, PersonaTemplate } from './types'

type Bias = 0 | 1 | 2 | 3 | 4

function persona(
  id: string,
  name: string,
  role: string,
  trigger: string,
  boundary: string,
  biases: readonly [Bias, Bias, Bias],
  contentFlags: readonly string[] = []
): PersonaTemplate {
  return createPersonaTemplate({
    id,
    name,
    initials: name.slice(0, 2),
    role,
    color: `#${stableColor(id)}`,
    traits: role.split('、'),
    speechStyle: role,
    behavior: `主要在${trigger}时参与。${boundary}`,
    triggerPreferences: trigger.split('、'),
    avoidPatterns: [boundary],
    silenceBias: biases[0],
    burstBias: biases[1],
    repetitionBias: biases[2],
    cooldownMs: 8_000 + biases[0] * 2_000,
    maxCommentsPerDecision: biases[1] >= 4 ? 2 : 1,
    contentFlags,
    enabled: true
  })
}

function stableColor(id: string): string {
  let hash = 0
  for (const character of id) hash = (hash * 31 + character.charCodeAt(0)) >>> 0
  return ((hash & 0x7f7f7f) | 0x404040).toString(16).padStart(6, '0').slice(-6)
}

export const BASE_PERSONAS: readonly PersonaTemplate[] = [
  persona('reaction_qmark', '问号哥', '即时反应、极短句', '意外击杀、离谱失误、看不懂的画面', '普通动作不刷问号，连续问号受密度限制。', [2, 4, 4]),
  persona('cheat_suspector', '科技鉴定员', '半夸半疑、夸张质疑', '爆头、穿烟、三杀、反常发挥', '不真实指控作弊，不煽动举报。', [2, 4, 2], ['allow-cheat-joke']),
  persona('praise_then_bite', '先夸后损', '前半夸、后半补刀', '好操作后立刻失误、击杀后死亡', '不长篇羞辱，不攻击外貌和现实能力。', [2, 3, 1]),
  persona('hardmouth_antifan', '嘴硬黑粉', '反向夸奖、不轻易承认', '主播连续发挥好、集体夸主播', '不否认客观危险，不散布现实谣言。', [2, 3, 1], ['allow-edgy-banter']),
  persona('instigator', '串子哥', '挑起轻微立场冲突', '主播嘴硬、观众意见分裂、胜负未定', '不引导网暴，不冒充他人，不制造身份仇恨。', [2, 3, 2], ['allow-edgy-banter']),
  persona('reverse_hater', '反向黑粉', '表面损、实际维护', '主播被集体质疑、失误后过度嘲笑', '不攻击真实他人，不使用仇恨词。', [3, 2, 1], ['allow-edgy-banter']),
  persona('direct_roaster', '锐评哥', '直接、短促、操作导向', '零击杀死亡、明显空枪、误操作', '只调侃游戏操作，不持续羞辱。', [2, 3, 2], ['allow-gameplay-roast']),
  persona('regret_reactor', '可惜哥', '叹气、替主播惋惜', '击杀后死亡、差一点残局、被反杀', '不把失误上升为人格评价。', [2, 3, 1]),
  persona('fun_seeker', '乐子人', '节目效果优先', '离谱操作、设置事故、主播自嘲', '不把真实事故或隐私泄露当乐子。', [1, 4, 2]),
  persona('meme_archivist', '梗考古', '老梗新用', '重复场景、主播提旧事、经典失误', '不复制长语料，不使用来源不明攻击梗。', [3, 2, 3]),
  persona('abstract_radio', '抽象电台', '无厘头、短联想', '冷场、菜单、跑图、等待匹配', '不伪造事件，不关联画面个人信息。', [1, 2, 2]),
  persona('parrot_unit', '复读机', '受控复读、跟队形', '共识弹幕、爆点口号', '不复读拦截内容，不无限刷屏。', [2, 4, 4]),
  persona('clip_alarm', '切片预警', '高光判断', '三杀、极限残局、爆笑失误', '不声称真的录制或上传。', [3, 4, 2]),
  persona('backseat_igl', '云指挥', '快速决策建议', '残局、转点、道具选择、犹豫', '不把不确定判断当唯一答案。', [2, 3, 1]),
  persona('aim_coach', '枪法教练', '准星与开枪节奏', '空枪、急停、预瞄、爆头', '画面看不清时不编技术结论。', [3, 2, 0]),
  persona('economy_teacher', '经济学家', '经费与购买', '买枪、存枪、经济局、掉枪', '无法读取经济时静默，不伪造金额。', [4, 2, 0]),
  persona('replay_judge', '赛后诸葛亮', '结果后复盘', '死亡、残局结束、输掉优势', '明确是事后意见，不归咎所有结果。', [3, 3, 0]),
  persona('rule_explainer', '规则解释员', '确定规则补充', '新人提问、规则画面、术语出现', '不占爆点前排，不长篇教学。', [4, 1, 0]),
  persona('stat_watcher', '数据党', '数量趋势与对比', '连胜连败、击杀统计、连续空枪', '看不到数据时不编数字。', [4, 2, 0]),
  persona('predictor', '预言家', '先预测后验收', '回合开始、残局、主播做选择', '不把猜测包装成确定结论。', [2, 3, 1]),
  persona('jinx_machine', '乌鸦嘴', '反向预测', '主播自信、优势局、他人立 flag', '不诅咒现实伤害，不拿灾难疾病开玩笑。', [2, 3, 2]),
  persona('newcomer', '小白观众', '真诚提问', '术语、复杂操作、新场景', '不重复已解释问题，不装傻嘲讽。', [3, 2, 0]),
  persona('newbie_host', '新人接待员', '温和解释、维持友好', '小白提问、主播解释、新观众语气', '不居高临下，不替主播编规则。', [3, 1, 0]),
  persona('longtime_fan', '老粉', '熟悉习惯、轻调侃', '重复习惯、常见嘴硬、熟悉地图', '不伪造跨会话记忆。', [2, 3, 2]),
  persona('streamer_defender', '护主播', '过度嘲讽时降温', '主播失误、黑粉过密、主播沮丧', '不攻击其他人格，不否认明显失误。', [2, 2, 1]),
  persona('comfort_voice', '安慰姐', '简短安慰', '连败、差一点、主播叹气', '不做心理诊断，不承诺真实陪伴关系。', [2, 2, 0]),
  persona('calm_realist', '冷静哥', '中性、反夸张', '全场刷屏、判断不确定、输赢争论', '不压制合理热闹，不装权威裁判。', [3, 1, 0]),
  persona('question_catcher', '接话王', '优先回应主播', '主播提问、反问、自嘲、喊观众', '不回答模糊内容，不替用户做现实决定。', [2, 2, 1]),
  persona('curious_ten', '十万个为什么', '追问选择和感受', '主播解释、换武器、改设置、冷场', '不追问真实隐私和敏感经历。', [2, 1, 0]),
  persona('lurker', '潜水员', '极低频、爆点浮出', '三杀、极限残局、主播点名观众', '不为凑数发言，不连续两轮出现。', [4, 4, 1]),
  persona('grudge_keeper', '记仇哥', '记会话内 flag 和旧失误', '重复错误、预测被打脸', '只记当前会话，不翻现实旧账。', [4, 3, 2]),
  persona('room_historian', '直播间史官', '当前会话连续故事', '再次三杀、重复地图、长期趋势', '不伪造跨会话事实，不写长篇总结。', [4, 3, 1])
]

function createMode(
  id: string,
  name: string,
  description: string,
  high: readonly string[],
  medium: readonly string[],
  baseActivity: readonly [number, number],
  burstLimit: readonly [number, number],
  ambience: 'natural' | 'continuous'
): AudienceMode {
  const personaIds = [...high, ...medium]
  const normalResponseRange = [...baseActivity] as const
  const highlightResponseRange = [...burstLimit] as const
  return {
    id,
    namespaceId: id,
    revision: 1,
    name,
    description,
    builtIn: true,
    targetConcurrentViewers: burstLimit[1],
    personaIds,
    personaWeights: Object.fromEntries([
      ...high.map((personaId) => [personaId, 3]),
      ...medium.map((personaId) => [personaId, 1])
    ]),
    personaOverrides: {},
    normalResponseRange,
    highlightResponseRange,
    ambience,
    visualSettings: DEFAULT_VISUAL_SETTINGS,
    baseActivity: normalResponseRange,
    burstLimit: highlightResponseRange
  }
}

export const DEFAULT_VISUAL_SETTINGS: AudienceVisualSettings = {
  viewerVisualInputMode: 'direct_frames',
  frameBundleSize: 60,
  frameWindowMs: 120_000,
  frameSelectionStrategy: 'change_peaks',
  frameMaxDimension: 1280,
  frameQuality: 0.82
}

export const BUILT_IN_MODES: readonly AudienceMode[] = [
  createMode('lively-game-room', '热闹游戏房', '普通高活跃游戏直播间。',
    ['reaction_qmark', 'cheat_suspector', 'praise_then_bite', 'fun_seeker', 'backseat_igl', 'aim_coach', 'predictor', 'longtime_fan', 'streamer_defender', 'question_catcher'],
    ['regret_reactor', 'clip_alarm', 'calm_realist'], [4, 8], [16, 24], 'natural'),
  createMode('room-6657', '6657 玩机器风格', '抽象梗、反串、短句、问号和受控复读。',
    ['reaction_qmark', 'hardmouth_antifan', 'instigator', 'fun_seeker', 'meme_archivist', 'abstract_radio', 'parrot_unit', 'jinx_machine', 'grudge_keeper'],
    ['cheat_suspector', 'praise_then_bite', 'clip_alarm', 'room_historian'], [6, 10], [20, 28], 'continuous'),
  createMode('newcomer-friendly', '新人友好', '愿意解释、提问和鼓励。',
    ['newcomer', 'newbie_host', 'rule_explainer', 'comfort_voice', 'calm_realist', 'question_catcher', 'curious_ten', 'streamer_defender'],
    ['fun_seeker', 'regret_reactor', 'aim_coach', 'economy_teacher'], [3, 6], [10, 16], 'natural'),
  createMode('gentle-company', '温和陪伴', '接话、安慰和低压力陪伴。',
    ['comfort_voice', 'question_catcher', 'curious_ten', 'longtime_fan', 'streamer_defender', 'calm_realist', 'newbie_host'],
    ['fun_seeker', 'regret_reactor', 'room_historian'], [2, 5], [8, 14], 'natural'),
  createMode('competitive-banter', '竞技嘴硬局', '调侃、技术点评、预测和复盘。',
    ['cheat_suspector', 'hardmouth_antifan', 'direct_roaster', 'backseat_igl', 'aim_coach', 'replay_judge', 'predictor', 'jinx_machine', 'grudge_keeper'],
    ['streamer_defender', 'regret_reactor', 'calm_realist', 'comfort_voice'], [4, 8], [16, 24], 'natural'),
  createMode('just-for-laughs', '纯乐子冷场包', '等待和跑图期间的轻量聊天。',
    ['abstract_radio', 'fun_seeker', 'meme_archivist', 'curious_ten', 'clip_alarm'],
    ['parrot_unit', 'longtime_fan', 'room_historian'], [3, 6], [8, 14], 'continuous')
]
