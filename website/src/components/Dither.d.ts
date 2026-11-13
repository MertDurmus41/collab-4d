// Types for the React Bits Dither.jsx next to it (the .jsx stays exactly as published).
import type { CSSProperties } from 'react'

export interface DitherProps {
  waveColor?: string | [number, number, number]
  backgroundColor?: string | [number, number, number]
  ditherMode?: 'bayer' | 'bayer4' | 'bayer2' | 'halftone' | 'lines' | 'crosshatch' | 'noise' | 'none'
  palette?: string[]
  colorNum?: number
  pixelSize?: number
  scale?: number
  waveSpeed?: number
  waveFrequency?: number
  waveAmplitude?: number
  contrast?: number
  brightness?: number
  enableMouseInteraction?: boolean
  mouseEffect?: 'erase' | 'reveal' | 'ripple'
  mouseRadius?: number
  mouseStrength?: number
  trail?: number
  clickRipple?: boolean
  intro?: boolean
  disableAnimation?: boolean
  dpr?: number
  className?: string
  style?: CSSProperties
}

declare const Dither: (props: DitherProps) => JSX.Element
export default Dither