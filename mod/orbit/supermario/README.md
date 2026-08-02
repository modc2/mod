# supermario

An NES emulator that runs in a browser tab. 🍄

This is the **console, not the cartridge**. Super Mario Bros. and the rest of
the library are still under copyright, so no game data ships with this module.
Load a dump of a cartridge you own — it is read in the page and never uploaded.

```
m supermario                 # what this is
m supermario/play            # serve it and open a browser
m supermario/serve           # run it under pm2 and register the gateway route
m supermario/boards          # which cartridge boards work
m supermario/test            # the whole test suite
```

Then drop a `.nes` file on the window.

## What is in here

| chip | file | what it does |
| --- | --- | --- |
| MOS 6502 | `web/js/cpu.js` | every official and unofficial opcode, exact cycle counts |
| RP2C02 | `web/js/ppu.js` | per-dot rendering, loopy scroll registers, sprite 0 hit |
| RP2A03 | `web/js/apu.js` | two pulses, triangle, noise and DPCM into WebAudio |
| boards | `web/js/mappers.js` | NROM, MMC1, UxROM, CNROM, MMC3, AxROM, GxROM |
| console | `web/js/nes.js` | memory map, DMA, controllers, save states |
| shell | `web/js/ui.js` | canvas, sound, pads, IndexedDB |

No build step and no dependencies: six plain scripts and one HTML file. The
core has no idea it is in a browser — `tests/` runs the same files under node.

### Cartridge boards

Seven boards cover most of the library, and all of the Mario games:

| # | board | games |
| --- | --- | --- |
| 0 | NROM | Super Mario Bros., Donkey Kong, Excitebike |
| 1 | MMC1 | Super Mario Bros. 2, Metroid, Zelda |
| 2 | UxROM | Castlevania, Contra, Mega Man |
| 3 | CNROM | Arkanoid, Gradius |
| 4 | MMC3 | Super Mario Bros. 3, Kirby, Mega Man 3–6 |
| 7 | AxROM | Battletoads |
| 66 | GxROM | Super Mario Bros. / Duck Hunt |

An unsupported board is reported by name rather than by a black screen.

## Controls

| | |
| --- | --- |
| D-pad | arrows or `WASD` |
| A | `X`, `K` or `space` |
| B | `Z` or `J` |
| Start / Select | `enter` / `shift` |
| Pause | `P` |
| Save / load state | `F2` / `F4` (four slots) |
| Fast forward | hold `tab` |

Any standard-layout gamepad works, for two players.

## What it keeps, and where

Everything local, in IndexedDB, in your browser:

- the last ROM you loaded, so a refresh does not mean finding the file again
- four save-state slots per cartridge
- battery saves for carts that have them, written a few seconds after the game
  stops touching them

Nothing is sent anywhere. The module serves static files and answers the
protocol's read-only functions; it has no upload path at all.

## Accuracy

Emulators are easy to make *nearly* work and hard to make right, so this one is
measured against the same public test ROMs everyone else uses. Fetch them with
`m supermario/fetch_test_roms` (they land in `~/.mod/supermario/testroms`,
off-tree — they are not this module's code) and run `m supermario/test`.

Passing today:

- **nestest** — all 8991 logged instructions match on PC, A, X, Y, P, SP *and*
  cycle count, unofficial opcodes included. This is the strongest single check
  on the CPU and it is diffed line by line in `tests/cputest.js`.
- **instr_test-v5** — all 16 groups, both `official_only` and `all_instrs`.
- **cpu_timing_test6** — instruction timing, PASSED.
- **blargg PPU 2005** — `palette_ram` and `vram_access`, both result code `$01`.
- **sprite_hit_tests** — basics, PASSED. This is the one Mario's status bar
  needs.
- **oam_read** — passed.
- **ppu_vbl_nmi** — 7 of 10: `vbl_basics`, `vbl_set_time`, `nmi_control`,
  `suppression`, `nmi_on_timing`, `even_odd_frames`, `even_odd_timing`.
- **mmc3_test_2** — 4 of 6: `clocking`, `details`, `A12_clocking`, `MMC3`.
  This is SMB3's scanline counter, so it is tested rather than assumed.

Beyond the test ROMs, the open-source demos in `nes-test-roms` render
correctly — `ny2011` (scrolling, sprites, music), `spritecans` (64 sprites with
flicker reduction), `full_palette` (all 64 colours plus emphasis, which needs
the "rendering disabled still outputs a colour" path), and `scroll` on MMC1.

Known gaps, stated plainly:

- `ppu_vbl_nmi` **03-vbl_clear_time**, **05-nmi_timing** and
  **08-nmi_off_timing** fail, and `mmc3_test_2` **4-scanline_timing** with
  them. All four are the same root cause: the PPU is clocked three dots at a
  time around each CPU memory access, so events land within one CPU cycle of
  the truth rather than on the exact dot. Games do not notice; those tests do.
- `mmc3_test_2` **6-MMC3_alt** tests the other MMC3 revision, whose reload
  behaviour contradicts `5-MMC3`. Passing both is not possible; this build
  takes the common revision.
- `apu_test` fails at **4-jitter** — the frame counter's IRQ phase is slightly
  early. The IRQ does fire once per frame, and the sound itself is correct.
- `cpu_dummy_reads` does not finish: the CPU does not perform the redundant
  read that indexed addressing does on real hardware. Invisible unless a game
  uses a dummy read to acknowledge a register.
- NTSC only. A PAL ROM will run at the wrong speed or not at all — `StarsSE`
  is one that hangs.
- No light gun, and no expansion audio (VRC6, FDS, MMC5).

## Tests

```
m supermario/fetch_test_roms   # once — public test ROMs, ~1MB
m supermario/test              # syntax + nestest diff + the ROM suite
python3 tests/smoke.py --shots # drives the real page in Chromium
node tests/cputest.js          # just the nestest diff
node tests/romtest.js <rom.nes> --png out.png   # run anything, dump the screen
```

`romtest.js` writes a PNG of the framebuffer, which is how the screen-only test
ROMs are read — and a good way to see what a misbehaving game is actually
drawing.

## Layout

```
config.json      port 50342, /supermario
mod.py           the anchor: info, serve, test, boards
serve.py         static bundle + the protocol API on one port
web/             the emulator (no build step)
tests/           cputest.js, romtest.js, smoke.py
```
