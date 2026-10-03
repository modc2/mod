'use client'

import { Component, type ReactNode } from 'react'
import { Mushroom } from './Sprites'

/**
 * The map, wrapped so that it can fail alone.
 *
 * MapLibre needs a WebGL 2 context, and it throws when it cannot get one —
 * inside an effect, which React propagates all the way up. Unguarded, that
 * takes down the whole tree: a headless capture box, a locked-down corporate
 * browser or a machine with blocklisted GPU drivers got a bare "Application
 * error: a client-side exception has occurred" and *nothing* else, even though
 * the layer rail, the housing controls, the tool docs and the ASK agent need no
 * GPU at all and were ready to render.
 *
 * So the map gets a boundary of its own. The console keeps working without it;
 * the panel below says which piece is missing and why, rather than leaving a
 * blank rectangle that reads as "still loading".
 */

/** Whether this browser can actually give MapLibre the context it needs. */
export function webglAvailable(): boolean {
  if (typeof document === 'undefined') return true // SSR: assume yes, recheck on mount
  try {
    const canvas = document.createElement('canvas')
    return !!canvas.getContext('webgl2')
  } catch {
    return false
  }
}

function Unavailable({ reason }: { reason: string }) {
  return (
    // Centred in the space the map actually had, not in the viewport: on a wide
    // screen the layer rail sits over the left 300px, and a panel centred past
    // it slides underneath and loses the start of every line. On a phone the
    // rail is a drawer, so there is nothing to clear.
    <div className="absolute inset-0 grid place-items-center bg-nes-void px-4
                    md:pl-[336px] md:pr-8">
      <div className="blk max-w-md px-5 py-7 text-center md:px-7">
        <div className="flex justify-center">
          <Mushroom size={40} />
        </div>
        <h2 className="pixel pixel-shadow mt-4 text-[13px] text-nes-coin">
          NO MAP HERE
        </h2>
        <p className="pixel mt-4 text-[8px] leading-[2.2] text-nes-ink2">
          THIS BROWSER CANT DRAW IT
        </p>
        <p className="mt-4 text-[12.5px] leading-relaxed text-nes-ink3">{reason}</p>
        <p className="mt-3 text-[12.5px] leading-relaxed text-nes-ink3">
          Everything else still works — open the layer rail for the data, or ask
          the agent a question. The same numbers are on{' '}
          <a className="text-nes-coin underline" href="/nyc/docs">the docs page</a>{' '}
          as an API and an MCP server.
        </p>
      </div>
    </div>
  )
}

type Props = { children: ReactNode }
type State = { failed: boolean; message: string }

export default class MapFrame extends Component<Props, State> {
  state: State = { failed: false, message: '' }

  static getDerivedStateFromError(error: unknown): State {
    return { failed: true, message: String((error as Error)?.message || error) }
  }

  componentDidCatch(error: unknown) {
    // Still worth a console line — the panel is deliberately not a stack trace.
    console.error('[nyc] map failed, console continues without it:', error)
  }

  render() {
    if (this.state.failed) {
      const gl = webglAvailable()
      return (
        <Unavailable
          reason={gl
            ? `The map renderer stopped: ${this.state.message}`
            : 'MapLibre needs WebGL 2, and this browser did not provide it — '
              + 'often a headless or remote session, a disabled GPU, or '
              + 'hardware acceleration turned off in settings.'}
        />
      )
    }
    return <>{this.props.children}</>
  }
}
