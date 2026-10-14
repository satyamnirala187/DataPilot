import logoUrl from '../assets/brand/datapilot-logo-color.svg'

/**
 * The approved DataPilot logo, as an image: never retyped, redrawn or recoloured. Size it with a
 * className (set the height; the width follows). Pass alt="" where the name is already announced
 * nearby, so screen readers do not read it twice.
 */
export default function Logo({ className, alt = 'DataPilot' }) {
  return <img src={logoUrl} alt={alt} className={className} />
}
