import type { SessionStatus } from '../../../../shared/session'

export function getLiveMessagePlaceholder({
  audienceSessionActive,
  sessionStatus,
  providerConfigured
}: {
  audienceSessionActive: boolean
  sessionStatus: SessionStatus
  providerConfigured: boolean
}): string {
  if (!audienceSessionActive) {
    return providerConfigured
      ? '开始直播后可与 AI 观众互动'
      : '配置供应商后可与 AI 观众互动'
  }
  return sessionStatus === 'running'
    ? '说点什么，AI 观众会回应你'
    : '开始直播后可发送'
}
