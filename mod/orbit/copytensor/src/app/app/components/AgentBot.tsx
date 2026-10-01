/**
 * The desk agent's face: a 12×12 sprite drawn on the pixel lattice, so it
 * sits in the arcade cabinet instead of looking like an icon-font import.
 * Body follows `currentColor`; the eyes and antenna light take the skin's
 * accent and blink in steps (CSS in globals.css under "AGENT DOCK").
 */
const BODY = [
  // [x, y, w, h] in sprite pixels
  [5, 1, 2, 1],   // antenna stalk (the tip is lit separately below)
  [2, 2, 8, 1],   // head top
  [1, 3, 1, 5],   // left cheek
  [10, 3, 1, 5],  // right cheek
  [2, 8, 8, 1],   // jaw
  [0, 4, 1, 3],   // left ear
  [11, 4, 1, 3],  // right ear
  [4, 6, 4, 1],   // mouth
  [3, 9, 6, 1],   // neck
  [2, 10, 8, 2],  // shoulders
] as const;

export default function AgentBot({ size = 18, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      shapeRendering="crispEdges"
      aria-hidden="true"
      className={`agent-bot ${className}`}
    >
      {BODY.map(([x, y, w, h], i) => (
        <rect key={i} x={x} y={y} width={w} height={h} fill="currentColor" />
      ))}
      <rect className="agent-bot__tip" x={5} y={0} width={2} height={1} />
      <rect className="agent-bot__eye" x={3} y={4} width={2} height={1} />
      <rect className="agent-bot__eye" x={7} y={4} width={2} height={1} />
    </svg>
  );
}
