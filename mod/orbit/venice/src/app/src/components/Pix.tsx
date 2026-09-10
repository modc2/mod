/**
 * Pixel glyphs, drawn as SVG rects on a 7×7 grid.
 *
 * Icon fonts and emoji are not an option here: the box a module is served
 * from may have no emoji font at all (📎 renders as tofu), and pixel fonts
 * like Press Start 2P carry no symbol range. These always draw, scale
 * crisply, and inherit `currentColor` so every theme tints them for free.
 */

const G: Record<string, string[]> = {
  sun: [
    "...#...",
    ".#...#.",
    "..###..",
    "#.###.#",
    "..###..",
    ".#...#.",
    "...#...",
  ],
  moon: [
    "..###..",
    ".##....",
    "###....",
    "###....",
    "###....",
    ".##....",
    "..###..",
  ],
  check: [
    "......#",
    ".....##",
    "#...##.",
    "##.##..",
    ".###...",
    "..#....",
    ".......",
  ],
  cross: [
    "#.....#",
    "##...##",
    ".##.##.",
    "..###..",
    ".##.##.",
    "##...##",
    "#.....#",
  ],
  image: [
    "#######",
    "#..#..#",
    "#.....#",
    "#...#.#",
    "#..####",
    "#.#####",
    "#######",
  ],
  menu: [
    ".......",
    "#######",
    ".......",
    "#######",
    ".......",
    "#######",
    ".......",
  ],
  left: [
    "....#..",
    "...#...",
    "..#....",
    ".#.....",
    "..#....",
    "...#...",
    "....#..",
  ],
  copy: [
    "..#####",
    "..#...#",
    "#####.#",
    "#...#.#",
    "#...###",
    "#...#..",
    "#####..",
  ],
  down: [
    ".......",
    "#######",
    ".#####.",
    "..###..",
    "...#...",
    ".......",
    ".......",
  ],
  plus: [
    "...#...",
    "...#...",
    "...#...",
    "#######",
    "...#...",
    "...#...",
    "...#...",
  ],
};

export type PixName = keyof typeof G;

export default function Pix({
  name,
  size = 14,
  className,
}: {
  name: PixName;
  size?: number;
  className?: string;
}) {
  const rows = G[name];
  return (
    <svg
      className={className ? `pix ${className}` : "pix"}
      width={size}
      height={size}
      viewBox="0 0 7 7"
      shapeRendering="crispEdges"
      aria-hidden="true"
      focusable="false"
    >
      {rows.map((row, y) =>
        row.split("").map((c, x) =>
          c === "#" ? <rect key={`${x}-${y}`} x={x} y={y} width="1" height="1" fill="currentColor" /> : null
        )
      )}
    </svg>
  );
}
