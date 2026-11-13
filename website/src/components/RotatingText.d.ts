// Types for the React Bits RotatingText.jsx next to it (the .jsx stays exactly as published).
import type { CSSProperties, ForwardRefExoticComponent, RefAttributes } from 'react'

export interface RotatingTextProps {
  texts: string[]
  effect?: 'slide' | 'flip' | 'blur' | 'scale' | 'drop'
  direction?: 'up' | 'down'
  highlight?: boolean
  highlightColor?: string
  rotationInterval?: number
  blur?: number
  staggerDuration?: number
  staggerFrom?: 'first' | 'last' | 'center' | 'random' | number
  splitBy?: string
  loop?: boolean
  auto?: boolean
  pauseOnHover?: boolean
  animatePresenceMode?: 'wait' | 'sync'
  animatePresenceInitial?: boolean
  onNext?: (index: number) => void
  mainClassName?: string
  splitLevelClassName?: string
  elementLevelClassName?: string
  style?: CSSProperties
}

export interface RotatingTextHandle {
  next: () => void
  previous: () => void
  jumpTo: (index: number) => void
  reset: () => void
}

declare const RotatingText: ForwardRefExoticComponent<RotatingTextProps & RefAttributes<RotatingTextHandle>>
export default RotatingText