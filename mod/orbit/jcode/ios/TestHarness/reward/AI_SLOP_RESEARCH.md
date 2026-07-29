# AI-Slop / Design-Authenticity Research (benchmark source)

Source material (fetched 2026-06-30) for the category F scorers
(`styling`, `simplicity`, `ai_patterns`). These are the documented, repeatable
tells of AI-generated UI we want to score AGAINST (higher reward = less slop).

References:
- prg.sh "Why Your AI Keeps Building the Same Purple Gradient Website"
- docs.bswen.com "How to Fix AI-Generated UI Designs: The Anti-Patterns Guide"
- noqta.tn "Escaping AI Slop: Fix the 4 Overused AI UI Patterns"
- Anthropic cookbook `frontend_aesthetics` prompt (quoted in prg.sh)

## The AI-slop tells (what to penalize)

Color / theme:
- Purple/indigo accents and purple->blue gradients. Canonical hexes:
  `#667eea`, `#764ba2`, `#8b5cf6`, `#A855F7`, `#6366F1` (indigo-500),
  `#7C3AED`, `#818CF8`. "Tailwind indigo-500" is the origin.
- Cyan-on-dark accents: `#38BDF8`, `#22D3EE`, neon accents on dark bg.
- Timid, evenly-distributed palettes (no dominant color + sharp accent).
- Gradient text for "impact" (gradient fills on numbers/headings).

Material / depth:
- Glassmorphism everywhere: blur / translucent material used decoratively
  rather than purposefully (SwiftUI: `.ultraThinMaterial`, `.regularMaterial`,
  `.blur(...)` sprinkled on many surfaces).
- Subtle shadows at exactly 0.1 opacity, applied uniformly.
- Giant/!uniform border radius on everything ("rounded-2xl on all").

Layout / structure:
- Card nesting: cards inside cards inside cards; everything wrapped in a
  container regardless of need.
- Three-features-in-boxes-with-icons grid (the SaaS cliche).
- Hero-metric layout: big number + small label + accent dot/line on the left.
- "Live" badges / status pills as decoration.

Typography:
- Generic fonts: Inter, Roboto, Arial, Open Sans, Lato, system defaults,
  Space Grotesk (the "even AI's escape hatch is now a tell").
- No real hierarchy beyond "bigger text = header"; single weight everywhere.

Content:
- Emoji used as UI iconography / bullets.
- Filler copy with no required-field indicators, no error/empty states.

## What good (non-slop) looks like (what to reward)

- A committed, cohesive aesthetic: one dominant color + sharp, sparing accent
  (NOT a timid rainbow). Our app: dark terminal-native + single mint accent
  `#4DD9A6`. That is intentional and NOT a purple default -> should score well.
- Distinctive typographic intent: deliberate mono vs. proportional pairing,
  real weight/size contrast (extremes, 3x size jumps), not one-size-fits-all.
- Depth from restraint: solid tokenized surfaces, hairline borders, blur used
  only where it earns its place (e.g. a single overlay), not everywhere.
- Flat hierarchy: avoid card-in-card nesting; use spacing + type as structure.
- Simplicity: few distinct UI primitives, low nesting depth, low element
  count per screen, generous negative space used on purpose.

## Mapping to OUR SwiftUI app (how to measure)

`styling` (F): aesthetic coherence & intent.
- SOURCE: count distinct accent colors actually used (palette cohesion: 1
  dominant accent good, many competing accents bad); presence of a real type
  scale (multiple deliberate sizes/weights via Theme.mono) vs. one size; radius
  drawn from a small consistent scale vs. arbitrary values.
- PIXEL: dominant-color cohesion, accent sparingness (accent should be a small
  % of pixels, used for emphasis, not everywhere).

`simplicity` (F): anti-complexity.
- SOURCE: view-nesting depth (max indent / brace depth of the densest view),
  count of distinct view primitives per screen, total modifier count per view,
  number of nested container shapes (RoundedRectangle/Card) stacked.
- PIXEL: number of distinct rectangular "card" regions; visual element count;
  reward fewer, larger, well-spaced regions over many small competing ones.

`ai_patterns` (F): anti-slop (higher = less AI-slop).
- SOURCE (primary): scan Theme.swift + views for slop hexes (purple/indigo/cyan
  list above), gradient usage (LinearGradient/.gradient) especially on text,
  `.ultraThinMaterial`/`.regularMaterial`/`.blur` overuse count, generic font
  names ("Inter"/"Roboto"/"Arial"/"Space Grotesk"), emoji in UI string
  literals, uniform 0.1-opacity shadows, oversized uniform corner radius,
  decorative "Live" badge. Each occurrence is a penalty; 0 occurrences = 100.
- PIXEL (corroboration): fraction of content pixels in the purple/indigo/cyan
  hue range; presence of large gradient regions.

Scoring note: our app currently uses mint (not purple), solid tokenized
surfaces, and a mono type system, so it should score HIGH on ai_patterns - the
scorer must reward that, and would only drop if someone introduced slop. The
one nuance: the app does have a "live" status pill; treat a SINGLE small status
pill as acceptable (it is functional state, not decoration), but flag if badges
proliferate.
