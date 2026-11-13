// Types for the React Bits Magnet.jsx next to it (the .jsx stays exactly as published).
import type { CSSProperties, HTMLAttributes, ReactNode } from 'react'

export interface MagnetProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  children: ReactNode
  padding?: number
  disabled?: boolean
  magnetStrength?: number
  maxOffset?: number
  exclusive?: boolean
  axis?: 'both' | 'x' | 'y'
  tilt?: number
  scale?: number
  spring?: { stiffness?: number; damping?: number; mass?: number }
  onActiveChange?: (active: boolean) => void
  wrapperClassName?: string
  innerClassName?: string
  style?: CSSProperties
}

declare const Magnet: (props: MagnetProps) => JSX.Element
export default Magnet
