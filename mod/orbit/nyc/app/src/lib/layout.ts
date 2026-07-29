/**
 * The one breakpoint the app has, mirrored from Tailwind's `md`.
 *
 * Below it the layout is a phone's: the map is full-bleed, the layer rail is a
 * drawer, the inspector is a sheet and the key folds into a chip. Above it,
 * every panel is on screen at once. It lives here rather than in a component
 * because both the markup and the map's own framing maths need it, and MapView
 * can only be imported for its types — it touches `window` at module load.
 */
export const NARROW = 768
