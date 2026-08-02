/* levels.js — the worlds of SUPER HOOD BROS.
 *
 * Levels are built with a tiny DSL instead of hand-typed ASCII maps: a map is
 * 15 rows tall and a few hundred columns wide, and typing that by hand is how
 * you get off-by-one holes in the sidewalk.
 *
 * Tile characters
 *   ' '  air              'X'  sidewalk / grass top   'D'  fill below it
 *   'A'  asphalt          'S'  stone / crate (solid)  '='  scaffold plank
 *   'B'  brownstone brick (breakable when big)
 *   '?'  bodega crate with something in it     'U'  spent crate
 *   'C'  tunnel ceiling   'W'  tunnel wall
 *   'L','R','l','r'  subway entrance: top-left, top-right, body-left, body-right
 */
(function (global) {
  'use strict';

  var H = 15;            // rows — exactly one screen tall, so no vertical scroll

  function Builder(name, opts) {
    opts = opts || {};
    this.name = name;
    this.world = opts.world || '1-1';
    this.theme = opts.theme || 'slope';
    this.music = opts.music || 'overworld';
    this.time = opts.time || 400;
    this.w = opts.w || 200;
    this.h = H;
    this.spawn = opts.spawn || { x: 3, y: 10 };
    this.grid = [];
    for (var y = 0; y < H; y++) {
      var row = [];
      for (var x = 0; x < this.w; x++) row.push(' ');
      this.grid.push(row);
    }
    this.foes = [];
    this.decor = [];
    this.plats = [];
    this.contents = {};      // "x,y" -> what a '?' block holds
    this.warps = [];
    this.goalX = null;
    this.exit = opts.exit || null;
    this.bossAt = null;
    this.gate = null;
    this.dark = !!opts.dark;
  }

  Builder.prototype.put = function (x, y, ch) {
    if (x < 0 || x >= this.w || y < 0 || y >= H) return this;
    this.grid[y][x] = ch;
    return this;
  };

  Builder.prototype.at = function (x, y) {
    if (x < 0 || x >= this.w || y < 0 || y >= H) return ' ';
    return this.grid[y][x];
  };

  Builder.prototype.fill = function (x0, y0, x1, y1, ch) {
    for (var y = y0; y <= y1; y++)
      for (var x = x0; x <= x1; x++) this.put(x, y, ch);
    return this;
  };

  /* A run of sidewalk: one surface row, solid fill beneath. */
  Builder.prototype.ground = function (x0, x1, top) {
    top = top === undefined ? 12 : top;
    for (var x = x0; x <= x1; x++) {
      this.put(x, top, 'X');
      for (var y = top + 1; y < H; y++) this.put(x, y, 'D');
    }
    return this;
  };

  /* Punch an open manhole / trackbed straight through the floor. */
  Builder.prototype.gap = function (x0, x1) {
    return this.fill(x0, 0, x1, H - 1, ' ');
  };

  Builder.prototype.row = function (x0, x1, y, ch) {
    return this.fill(x0, y, x1, y, ch);
  };

  /* A crate with something inside. kind: coin | pizza | star | 1up | multi */
  Builder.prototype.crate = function (x, y, kind) {
    this.put(x, y, '?');
    this.contents[x + ',' + y] = kind || 'coin';
    return this;
  };

  /* A brick that secretly holds something (bump it to find out). */
  Builder.prototype.secret = function (x, y, kind) {
    this.put(x, y, 'B');
    this.contents[x + ',' + y] = kind || 'coin';
    return this;
  };

  /* A subway entrance. Two tiles wide, `h` tall, top row at `top`. */
  Builder.prototype.pipe = function (x, top, h, warp) {
    this.put(x, top, 'L'); this.put(x + 1, top, 'R');
    for (var y = top + 1; y < top + h; y++) {
      this.put(x, y, 'l'); this.put(x + 1, y, 'r');
    }
    if (warp) this.warps.push({ x: x, y: top, w: 2, to: warp.to, at: warp.at });
    return this;
  };

  /* A staircase of crates, `n` steps, rising to the right (dir 1) or left. */
  Builder.prototype.stair = function (x, baseY, n, dir) {
    dir = dir || 1;
    for (var i = 0; i < n; i++) {
      var cx = x + i * dir;
      for (var y = baseY; y > baseY - (i + 1); y--) this.put(cx, y, 'S');
    }
    return this;
  };

  Builder.prototype.coins = function (x, y, n, step) {
    step = step || 1;
    for (var i = 0; i < n; i++) this.foes.push({ kind: 'token', x: x + i * step, y: y });
    return this;
  };

  Builder.prototype.foe = function (kind, x, y, opts) {
    var e = { kind: kind, x: x, y: y };
    for (var k in (opts || {})) e[k] = opts[k];
    this.foes.push(e);
    return this;
  };

  Builder.prototype.deco = function (kind, x, y) {
    this.decor.push({ kind: kind, x: x, y: y });
    return this;
  };

  /* A moving scaffold plank. axis 'x' | 'y', range in tiles. */
  Builder.prototype.plat = function (x, y, wTiles, axis, range, speed) {
    this.plats.push({ x: x, y: y, w: wTiles, axis: axis || 'y',
                      range: range || 3, speed: speed || 0.4 });
    return this;
  };

  Builder.prototype.goal = function (x, top) {
    this.goalX = x;
    this.goalTop = top === undefined ? 4 : top;
    return this;
  };

  Builder.prototype.done = function () { return this; };

  /* ── WORLD 1-1 — PARK SLOPE ─────────────────────────────────────────
   * Fifth Avenue on a Saturday: stoops, scaffolding, crates outside the
   * bodega, pigeons that will not move for anybody.
   */
  function level_1_1() {
    var L = new Builder('PARK SLOPE', { world: '1-1', theme: 'slope',
                                        music: 'overworld', w: 214, time: 400 });
    L.ground(0, 68, 12);
    L.ground(72, 85, 12);
    L.ground(89, 152, 12);
    L.ground(156, 213, 12);

    // the block you start on
    L.deco('hydrant', 6, 11);
    L.deco('tree', 11, 11);
    L.crate(16, 8, 'coin');
    L.put(20, 8, 'B'); L.crate(21, 8, 'pizza'); L.put(22, 8, 'B');
    L.crate(21, 4, 'coin');
    L.deco('trash', 26, 11);

    // first pigeon, then two more on the wide stretch
    L.foe('pigeon', 24, 11);
    L.foe('pigeon', 41, 11);
    L.foe('pigeon', 43, 11);

    // subway entrances, getting deeper
    L.pipe(30, 10, 3);
    L.pipe(39, 9, 4);
    L.pipe(48, 8, 5);
    L.deco('tree', 35, 11);

    // the warp entrance — go down here for the token cellar
    L.pipe(56, 8, 5, { to: 'cellar', at: { x: 3, y: 10 } });
    L.foe('rat', 62, 11);

    // open manhole #1
    L.deco('bush', 66, 11);

    // scaffolding over the sidewalk after the gap
    L.row(74, 82, 8, '=');
    L.coins(75, 6, 6);
    L.foe('pigeon', 78, 7);
    L.foe('rat', 81, 11);

    // brownstone brick shelf with a hidden egg sandwich
    L.put(93, 8, 'B'); L.crate(94, 8, 'coin'); L.put(95, 8, 'B');
    L.secret(97, 8, '1up');
    L.put(99, 8, 'B'); L.crate(100, 8, 'multi'); L.put(101, 8, 'B');
    L.foe('pigeon', 104, 11);
    L.foe('pigeon', 106, 11);

    // the double-decker: bricks below, crates above
    L.row(110, 118, 8, 'B');
    L.row(112, 116, 4, 'B');
    L.crate(114, 4, 'star');
    L.coins(111, 6, 7);
    L.foe('rat', 120, 11);

    // scaffold hopping over the second manhole
    L.pipe(125, 10, 3);
    L.deco('trash', 130, 11);
    L.row(134, 137, 7, '=');
    L.row(141, 144, 5, '=');
    L.coins(141, 3, 4);
    L.foe('pigeon', 143, 4);
    L.row(148, 151, 7, '=');

    // manhole #2 at 153..155, then the run-up to the corner
    L.foe('rat', 160, 11);
    L.foe('pigeon', 163, 11);
    L.crate(166, 8, 'pizza');
    L.put(170, 8, 'B'); L.put(171, 8, 'B'); L.put(172, 8, 'B');
    L.foe('pigeon', 175, 11);
    L.deco('tree', 168, 11);

    // crate staircase up to the lamppost
    L.stair(180, 11, 4, 1);
    L.stair(190, 11, 4, -1);
    L.foe('rat', 186, 7);

    L.deco('bush', 196, 11);
    L.goal(200, 3);
    return L.done();
  }

  /* ── THE TOKEN CELLAR — the warp room under Fifth Avenue ───────────── */
  function level_cellar() {
    var L = new Builder('TOKEN CELLAR', { world: '1-1', theme: 'tunnel',
                                          music: 'tunnel', w: 32, time: 200,
                                          dark: true, spawn: { x: 3, y: 2 },
                                          exit: { to: '1-1', at: { x: 110, y: 10 } } });
    L.fill(0, 0, 31, 1, 'C');
    L.ground(0, 31, 12);
    L.fill(0, 2, 0, 11, 'W');
    L.fill(31, 2, 31, 11, 'W');
    for (var r = 0; r < 4; r++) L.coins(4, 4 + r * 2, 10, 2);
    L.coins(6, 10, 8, 2);
    L.crate(14, 6, '1up');
    L.pipe(27, 10, 3);          // the way back up
    L.exitPipe = { x: 27, y: 10, w: 2 };
    return L.done();
  }

  /* ── WORLD 1-2 — G TRAIN ────────────────────────────────────────────
   * Down on the platform, then out along the trackbed. Rats own this level.
   */
  function level_1_2() {
    var L = new Builder('G TRAIN', { world: '1-2', theme: 'tunnel',
                                     music: 'tunnel', w: 196, time: 400,
                                     dark: true, spawn: { x: 3, y: 10 } });
    L.fill(0, 0, 195, 1, 'C');
    L.ground(0, 44, 12);
    L.fill(0, 2, 0, 11, 'W');

    // the platform: benches of stone, a rat under every one
    L.crate(8, 8, 'pizza');
    L.row(12, 15, 8, 'S');
    L.foe('rat', 14, 11);
    L.foe('rat', 20, 11);
    L.coins(12, 6, 4);
    L.row(24, 30, 6, 'B');
    L.secret(27, 6, 'coin');
    L.coins(24, 4, 7);
    L.foe('pigeon', 33, 11);
    L.row(36, 41, 9, '=');
    L.foe('rat', 38, 8);

    // the trackbed — gaps, and planks over the third rail
    L.ground(48, 60, 12);
    L.foe('rat', 52, 11);
    L.crate(55, 8, 'coin'); L.crate(56, 8, 'multi');
    L.plat(63, 8, 3, 'x', 6, 0.45);
    L.ground(72, 84, 12);
    L.foe('rat', 76, 11);
    L.foe('pigeon', 80, 11);
    L.row(74, 79, 7, 'B');
    L.secret(77, 7, 'star');
    L.plat(88, 9, 3, 'y', 4, 0.4);
    L.plat(95, 6, 3, 'y', 4, 0.4);
    L.ground(101, 118, 12);
    L.foe('rat', 106, 11);
    L.foe('rat', 108, 11);
    L.crate(112, 8, 'pizza');
    L.row(110, 115, 4, 'S');
    L.coins(110, 3, 6);

    // the long stretch under the river: stone pillars and a tight ceiling
    L.ground(122, 195, 12);
    L.fill(0, 0, 195, 2, 'C');
    for (var i = 0; i < 5; i++) {
      var px = 128 + i * 12;
      L.fill(px, 6, px, 11, 'S');
      L.fill(px + 1, 6, px + 1, 11, 'S');
      L.foe('rat', px + 6, 11);
      L.crate(px + 5, 7, i === 2 ? '1up' : 'coin');
    }
    L.foe('pigeon', 150, 8);
    L.foe('pigeon', 168, 8);
    L.coins(140, 8, 6);
    L.stair(182, 11, 4, 1);
    L.goal(190, 3);
    return L.done();
  }

  /* ── WORLD 1-3 — WASHINGTON PARK ────────────────────────────────────
   * Out into the green, up onto the ballfield wall, and then the landlord
   * turns up wanting a fifteen-hundred-dollar raise.
   */
  function level_1_3() {
    var L = new Builder('WASHINGTON PARK', { world: '1-3', theme: 'park',
                                             music: 'park', w: 212, time: 400 });
    L.ground(0, 40, 12);
    L.ground(44, 96, 12);
    L.ground(100, 211, 12);

    L.deco('tree', 5, 11);
    L.deco('tree', 9, 11);
    L.deco('bush', 14, 11);
    L.crate(12, 8, 'pizza');
    L.foe('gull', 18, 6);
    L.foe('pigeon', 22, 11);

    // the low park wall
    L.row(24, 30, 9, 'S');
    L.coins(25, 7, 5);
    L.foe('pigeon', 27, 8);
    L.deco('tree', 34, 11);
    L.foe('rat', 36, 11);

    // the path over the ballfield
    L.plat(41, 9, 3, 'x', 3, 0.4);
    L.row(48, 54, 7, 'B');
    L.secret(51, 7, 'star');
    L.crate(53, 7, 'multi');
    L.foe('gull', 58, 5);
    L.foe('gull', 64, 7);
    L.foe('pigeon', 61, 11);
    L.deco('bush', 68, 11);
    L.row(70, 76, 5, '=');
    L.coins(70, 3, 7);
    L.foe('rat', 80, 11);
    L.foe('rat', 82, 11);
    L.crate(86, 8, 'coin');
    L.put(87, 8, 'B'); L.secret(88, 8, '1up'); L.put(89, 8, 'B');
    L.stair(92, 11, 3, 1);

    // the bandshell
    L.ground(100, 211, 12);
    L.deco('tree', 103, 11);
    L.row(106, 118, 6, 'S');
    L.coins(107, 4, 11);
    L.foe('gull', 112, 3);
    L.foe('pigeon', 110, 5);
    L.foe('rat', 122, 11);
    L.crate(126, 8, 'pizza');
    L.row(130, 136, 8, 'B');
    L.secret(133, 8, 'star');
    L.foe('gull', 140, 6);
    L.foe('pigeon', 144, 11);
    L.deco('bush', 148, 11);
    L.crate(150, 8, 'multi');
    L.foe('rat', 154, 11);

    // the Old Stone House yard — the arena
    L.deco('tree', 158, 11);
    L.row(162, 164, 7, 'S');
    L.row(184, 186, 7, 'S');
    L.crate(163, 4, 'pizza');
    L.crate(185, 4, 'coin');
    L.bossAt = { x: 180, y: 9 };
    L.gate = { x: 196, y0: 6, y1: 12 };
    L.fill(196, 6, 196, 12, 'S');

    L.deco('tree', 200, 11);
    L.goal(205, 3);
    return L.done();
  }

  var ORDER = ['1-1', '1-2', '1-3'];
  var MAKERS = { '1-1': level_1_1, '1-2': level_1_2, '1-3': level_1_3,
                 'cellar': level_cellar };

  global.LEVELS = {
    order: ORDER,
    make: function (id) {
      var f = MAKERS[id];
      if (!f) throw new Error('no such level: ' + id);
      return f();
    },
    ids: function () { return Object.keys(MAKERS); },
    H: H
  };
})(window);
