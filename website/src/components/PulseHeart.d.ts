// Types for the React Bits PulseHeart.jsx next to it (the .jsx stays exactly as published).
export interface PulseHeartProps {
  liked?: boolean
  defaultLiked?: boolean
  count?: number
  onChange?: (liked: boolean, count: number) => void
  showCount?: boolean
  icon?: 'heart' | 'star' | 'thumb' | React.ReactNode
  idleOutline?: boolean
  size?: number
  corner?: number
  likedColor?: string
  idleColor?: string
  pillColor?: string
  textColor?: string
  duration?: number
  dotSize?: number
  overshoot?: number
  beat?: number
  rollDuration?: number
  disabled?: boolean
  label?: string
  className?: string
}

declare const PulseHeart: (props: PulseHeartProps) => JSX.Element
export default PulseHeart
