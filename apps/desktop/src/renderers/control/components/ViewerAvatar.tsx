import {
  Bot,
  CircleDot,
  Cpu,
  Gamepad2,
  Headphones,
  MessageCircle,
  Radio,
  Sparkles,
  type LucideIcon
} from 'lucide-react'
import {
  viewerAvatarTone,
  viewerAvatarVariant
} from './viewerAvatarUtils'
import './viewer-avatar.css'

const AVATAR_ICONS: readonly LucideIcon[] = [
  Bot,
  CircleDot,
  Cpu,
  Gamepad2,
  Headphones,
  MessageCircle,
  Radio,
  Sparkles
]

export type ViewerAvatarProps = {
  avatarSeed: string
  colorSeed: string
  className?: string
}

export function ViewerAvatar({
  avatarSeed,
  colorSeed,
  className
}: ViewerAvatarProps): React.JSX.Element {
  const variant = viewerAvatarVariant(avatarSeed)
  const tone = viewerAvatarTone(colorSeed)
  const AvatarIcon = AVATAR_ICONS[variant]

  return (
    <span
      aria-hidden="true"
      className={`viewer-avatar-visual${className ? ` ${className}` : ''}`}
      data-avatar-tone={tone}
      data-avatar-variant={variant}
    >
      <AvatarIcon aria-hidden="true" />
    </span>
  )
}
