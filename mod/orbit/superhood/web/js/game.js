/* game.js — SUPER HOOD BROS.: WASHINGTON PARK
 *
 * A 60Hz fixed-step platformer on a 256x240 backbuffer, scaled up with nearest
 * neighbour so it stays crunchy. No dependencies, no assets on disk: every
 * pixel is drawn from the grids in sprites.js and every sound is synthesised.
 */
(function (global) {
  'use strict';

  var VW = 256, VH = 240, T = 16;
  var STEP = 1 / 60;

  /* ── a 5x7 bitmap font ─────────────────────────────────────────────── */

  var GLYPHS = {
    A: '.###. #...# #...# ##### #...# #...# #...#',
    B: '####. #...# #...# ####. #...# #...# ####.',
    C: '.###. #...# #.... #.... #.... #...# .###.',
    D: '####. #...# #...# #...# #...# #...# ####.',
    E: '##### #.... #.... ####. #.... #.... #####',
    F: '##### #.... #.... ####. #.... #.... #....',
    G: '.###. #...# #.... #.### #...# #...# .###.',
    H: '#...# #...# #...# ##### #...# #...# #...#',
    I: '##### ..#.. ..#.. ..#.. ..#.. ..#.. #####',
    J: '..### ...#. ...#. ...#. ...#. #..#. .##..',
    K: '#...# #..#. #.#.. ##... #.#.. #..#. #...#',
    L: '#.... #.... #.... #.... #.... #.... #####',
    M: '#...# ##.## #.#.# #.#.# #...# #...# #...#',
    N: '#...# ##..# #.#.# #..## #...# #...# #...#',
    O: '.###. #...# #...# #...# #...# #...# .###.',
    P: '####. #...# #...# ####. #.... #.... #....',
    Q: '.###. #...# #...# #...# #.#.# #..#. .##.#',
    R: '####. #...# #...# ####. #.#.. #..#. #...#',
    S: '.#### #.... #.... .###. ....# ....# ####.',
    T: '##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..',
    U: '#...# #...# #...# #...# #...# #...# .###.',
    V: '#...# #...# #...# #...# #...# .#.#. ..#..',
    W: '#...# #...# #...# #.#.# #.#.# ##.## #...#',
    X: '#...# #...# .#.#. ..#.. .#.#. #...# #...#',
    Y: '#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..',
    Z: '##### ....# ...#. ..#.. .#... #.... #####',
    '0': '.###. #...# #..## #.#.# ##..# #...# .###.',
    '1': '..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.',
    '2': '.###. #...# ....# ...#. ..#.. .#... #####',
    '3': '####. ....# ....# .###. ....# ....# ####.',
    '4': '...#. ..##. .#.#. #..#. ##### ...#. ...#.',
    '5': '##### #.... ####. ....# ....# #...# .###.',
    '6': '.###. #...# #.... ####. #...# #...# .###.',
    '7': '##### ....# ...#. ..#.. .#... .#... .#...',
    '8': '.###. #...# #...# .###. #...# #...# .###.',
    '9': '.###. #...# #...# .#### ....# #...# .###.',
    '-': '..... ..... ..... ##### ..... ..... .....',
    '.': '..... ..... ..... ..... ..... .##.. .##..',
    ',': '..... ..... ..... ..... .##.. .##.. .#...',
    '!': '..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..',
    '?': '.###. #...# ....# ..##. ..#.. ..... ..#..',
    ':': '..... .##.. .##.. ..... .##.. .##.. .....',
    '*': '..#.. #.#.# .###. ..#.. .###. #.#.# ..#..',
    '/': '....# ...#. ...#. ..#.. .#... .#... #....',
    "'": '..#.. ..#.. ..... ..... ..... ..... .....',
    '(': '...#. ..#.. .#... .#... .#... ..#.. ...#.',
    ')': '.#... ..#.. ...#. ...#. ...#. ..#.. .#...',
    '+': '..... ..#.. ..#.. ##### ..#.. ..#.. .....',
    '>': '#.... .#... ..#.. ...#. ..#.. .#... #....',
    '<': '....# ...#. ..#.. .#... ..#.. ...#. ....#',
    '=': '..... ##### ..... ..... ##### ..... .....',
    '$': '..#.. .#### #.#.. .###. ..#.# ####. ..#..'
  };

  var fontCache = {};
  function glyph(ch, color) {
    var key = ch + color;
    if (fontCache[key]) return fontCache[key];
    var rows = (GLYPHS[ch] || '').split(' ');
    var cv = document.createElement('canvas');
    cv.width = 5; cv.height = 7;
    var g = cv.getContext('2d');
    g.fillStyle = color;
    for (var y = 0; y < rows.length; y++)
      for (var x = 0; x < rows[y].length; x++)
        if (rows[y][x] === '#') g.fillRect(x, y, 1, 1);
    fontCache[key] = cv;
    return cv;
  }

  function text(g, str, x, y, color, scale) {
    scale = scale || 1;
    color = color || '#ffffff';
    str = String(str).toUpperCase();
    for (var i = 0; i < str.length; i++) {
      var ch = str[i];
      if (ch !== ' ' && GLYPHS[ch]) {
        var cv = glyph(ch, color);
        g.drawImage(cv, x + i * 6 * scale, y, 5 * scale, 7 * scale);
      }
    }
    return x + str.length * 6 * scale;
  }

  function textCentered(g, str, y, color, scale) {
    scale = scale || 1;
    var w = String(str).length * 6 * scale;
    return text(g, str, Math.round((VW - w) / 2), y, color, scale);
  }

  /* ── tiles ─────────────────────────────────────────────────────────── */

  var SOLID = { X: 1, D: 1, A: 1, S: 1, B: 1, '?': 1, U: 1, C: 1, W: 1,
                '=': 1, L: 1, R: 1, l: 1, r: 1 };

  var THEME = {
    slope: { sky: ['#5c94fc', '#8fc3ff'], top: '#c9c9c9', topEdge: '#f0f0f0',
             fill: '#8a8a8a', fillDark: '#5e5e5e', brick: '#b3542e',
             brickDark: '#7c3418', mortar: '#e8c9a0' },
    park:  { sky: ['#4f8fe8', '#a8d8ff'], top: '#4aa83c', topEdge: '#79d45e',
             fill: '#7a5230', fillDark: '#54371e', brick: '#b3542e',
             brickDark: '#7c3418', mortar: '#e8c9a0' },
    tunnel:{ sky: ['#04040c', '#0a0a1c'], top: '#5b4a3a', topEdge: '#7d6650',
             fill: '#38302a', fillDark: '#221c18', brick: '#5c3a6e',
             brickDark: '#3a2247', mortar: '#c9b8d8' }
  };

  var tileCache = {};
  function tile(ch, theme, frame) {
    var key = ch + theme + (frame || 0);
    if (tileCache[key]) return tileCache[key];
    var th = THEME[theme] || THEME.slope;
    var cv = document.createElement('canvas');
    cv.width = T; cv.height = T;
    var g = cv.getContext('2d');
    var i;

    function bevel(base, light, dark) {
      g.fillStyle = base; g.fillRect(0, 0, T, T);
      g.fillStyle = light; g.fillRect(0, 0, T, 2); g.fillRect(0, 0, 2, T);
      g.fillStyle = dark; g.fillRect(0, T - 2, T, 2); g.fillRect(T - 2, 0, 2, T);
    }

    switch (ch) {
      case 'X':   // the walking surface
        g.fillStyle = th.fill; g.fillRect(0, 0, T, T);
        g.fillStyle = th.top; g.fillRect(0, 0, T, 6);
        g.fillStyle = th.topEdge; g.fillRect(0, 0, T, 2);
        if (theme === 'park') {
          g.fillStyle = th.topEdge;
          for (i = 0; i < T; i += 3) g.fillRect(i, 2, 1, 3);
          g.fillStyle = th.fill; g.fillRect(0, 6, T, T - 6);
        } else {
          g.fillStyle = th.fillDark;
          g.fillRect(0, 6, T, 1); g.fillRect(7, 6, 2, T - 6);
        }
        break;
      case 'D':   // packed fill below the surface
        g.fillStyle = th.fill; g.fillRect(0, 0, T, T);
        g.fillStyle = th.fillDark;
        g.fillRect(0, 0, T, 1);
        g.fillRect(3, 4, 2, 2); g.fillRect(11, 9, 2, 2); g.fillRect(6, 12, 2, 2);
        break;
      case 'A':
        g.fillStyle = '#3c3c44'; g.fillRect(0, 0, T, T);
        g.fillStyle = '#4c4c56'; g.fillRect(0, 0, T, 2);
        break;
      case 'S':   // MTA-issue stone block
        bevel('#9a9aa8', '#c8c8d6', '#5c5c68');
        g.fillStyle = '#7c7c88';
        g.fillRect(4, 4, 8, 8);
        g.fillStyle = '#5c5c68';
        g.fillRect(3, 3, 2, 2); g.fillRect(11, 3, 2, 2);
        g.fillRect(3, 11, 2, 2); g.fillRect(11, 11, 2, 2);
        break;
      case 'B':   // brownstone brick
        g.fillStyle = th.mortar; g.fillRect(0, 0, T, T);
        g.fillStyle = th.brick;
        g.fillRect(0, 1, 7, 6); g.fillRect(8, 1, 7, 6);
        g.fillRect(0, 9, 3, 6); g.fillRect(4, 9, 7, 6); g.fillRect(12, 9, 4, 6);
        g.fillStyle = th.brickDark;
        g.fillRect(0, 6, 7, 1); g.fillRect(8, 6, 7, 1);
        g.fillRect(0, 14, 3, 1); g.fillRect(4, 14, 7, 1); g.fillRect(12, 14, 4, 1);
        break;
      case '?':   // a crate outside the bodega
        var pulse = [0, 1, 2, 1][frame || 0];
        var bases = ['#e8a020', '#f0b028', '#f8c840'];
        bevel(bases[pulse], '#ffe08a', '#a05c08');
        g.fillStyle = '#7a4404';
        g.fillRect(3, 3, 1, 1); g.fillRect(12, 3, 1, 1);
        g.fillRect(3, 12, 1, 1); g.fillRect(12, 12, 1, 1);
        var q = glyph('?', '#7a4404');
        g.drawImage(q, 5, 4);
        break;
      case 'U':   // that crate, emptied
        bevel('#9a6a30', '#b98a4c', '#5c3c14');
        g.fillStyle = '#5c3c14';
        g.fillRect(3, 3, 2, 2); g.fillRect(11, 3, 2, 2);
        g.fillRect(3, 11, 2, 2); g.fillRect(11, 11, 2, 2);
        break;
      case '=':   // scaffold plank
        g.fillStyle = '#a8762e'; g.fillRect(0, 0, T, T);
        g.fillStyle = '#c89040'; g.fillRect(0, 0, T, 4);
        g.fillStyle = '#6c4614'; g.fillRect(0, 10, T, 6);
        g.fillStyle = '#8c8c98'; g.fillRect(0, 4, T, 2); g.fillRect(0, 9, T, 1);
        break;
      case 'C':   // tunnel ceiling
        g.fillStyle = '#241d2c'; g.fillRect(0, 0, T, T);
        g.fillStyle = '#33293e'; g.fillRect(0, T - 3, T, 3);
        g.fillStyle = '#1a1420'; g.fillRect(4, 2, 8, 3);
        break;
      case 'W':   // glossy white subway tile
        g.fillStyle = '#cfcfd8'; g.fillRect(0, 0, T, T);
        g.fillStyle = '#eef0f4'; g.fillRect(1, 1, 6, 6); g.fillRect(9, 1, 6, 6);
        g.fillRect(1, 9, 6, 6); g.fillRect(9, 9, 6, 6);
        g.fillStyle = '#ffffff'; g.fillRect(1, 1, 6, 1); g.fillRect(9, 9, 6, 1);
        break;
      case 'L': case 'R': case 'l': case 'r': {
        var lip = (ch === 'L' || ch === 'R');
        var left = (ch === 'L' || ch === 'l');
        g.fillStyle = '#1f7a3a'; g.fillRect(0, 0, T, T);
        g.fillStyle = '#35b05a'; g.fillRect(left ? 2 : 6, 0, 8, T);
        g.fillStyle = '#0d4a22';
        if (left) g.fillRect(0, 0, 2, T); else g.fillRect(T - 2, 0, 2, T);
        if (lip) {
          g.fillStyle = '#d8d8e0'; g.fillRect(0, 0, T, 4);
          g.fillStyle = '#8c8c98'; g.fillRect(0, 4, T, 2);
        }
        break;
      }
      default:
        return null;
    }
    tileCache[key] = cv;
    return cv;
  }

  /* ── input ─────────────────────────────────────────────────────────── */

  var KEYMAP = {
    ArrowLeft: 'left', KeyA: 'left',
    ArrowRight: 'right', KeyD: 'right',
    ArrowUp: 'up', KeyW: 'up',
    ArrowDown: 'down', KeyS: 'down',
    Space: 'jump', KeyZ: 'jump', KeyK: 'jump',
    ShiftLeft: 'run', ShiftRight: 'run', KeyX: 'run', KeyJ: 'run',
    Enter: 'start', KeyP: 'pause', KeyM: 'mute'
  };

  var keys = {}, pressed = {}, sampled = {}, pendingUp = {};

  // A one-shot press stays live for a few frames instead of exactly one, so a
  // jump asked for just before landing still fires. Together with the coyote
  // frames in updatePlayer this is what makes the controls feel tight.
  var EDGE_FRAMES = 7;

  function press(name) {
    if (!keys[name]) { pressed[name] = EDGE_FRAMES; sampled[name] = false; }
    keys[name] = true;
    pendingUp[name] = false;
  }

  // A tap can begin and end between two frames — on a touch pad it usually
  // does, and the input then vanishes. Keep the key down until one update has
  // actually read it.
  function release(name) {
    if (keys[name] && !sampled[name]) { pendingUp[name] = true; return; }
    keys[name] = false;
  }

  function consume(name) {
    if (pressed[name] > 0) { pressed[name] = 0; return true; }
    return false;
  }

  function clearEdges() { pressed = {}; }

  function clearKeys() {
    keys = {}; pressed = {}; sampled = {}; pendingUp = {};
  }

  // Once per fixed step, after update() has read the input.
  function ageInput() {
    for (var k in keys) {
      if (keys[k]) sampled[k] = true;
      if (pendingUp[k] && sampled[k]) { keys[k] = false; pendingUp[k] = false; }
    }
    for (var e in pressed) if (pressed[e] > 0) pressed[e]--;
  }

  /* ── entities ──────────────────────────────────────────────────────── */

  function Ent(kind, x, y, w, h) {
    this.kind = kind;
    this.x = x; this.y = y; this.w = w; this.h = h;
    this.vx = 0; this.vy = 0;
    this.dir = -1;
    this.onGround = false;
    this.dead = false;
    this.remove = false;
    this.t = 0;
    this.state = '';
  }

  Ent.prototype.overlaps = function (o) {
    return this.x < o.x + o.w && this.x + this.w > o.x &&
           this.y < o.y + o.h && this.y + this.h > o.y;
  };

  /* ── the game ──────────────────────────────────────────────────────── */

  function Game(canvas) {
    this.cv = canvas;
    this.g = canvas.getContext('2d');
    this.g.imageSmoothingEnabled = false;
    this.frame = 0;
    this.mode = 'title';
    this.score = 0;
    this.tokens = 0;
    this.lives = 3;
    this.levelId = LEVELS.order[0];
    this.high = 0;
    try { this.high = parseInt(localStorage.getItem('superhood.high') || '0', 10) || 0; } catch (e) {}
    this.acc = 0;
    this.last = 0;
    this.shake = 0;
    this.titleT = 0;
    this.paused = false;
    this.msg = null;
  }

  Game.prototype.loadLevel = function (id, spawnAt, keepStats) {
    var lv = LEVELS.make(id);
    this.levelId = id;
    this.level = lv;
    this.theme = lv.theme;
    this.time = lv.time;
    this.timeSub = 0;
    this.cam = { x: 0 };
    this.ents = [];
    this.parts = [];
    this.pops = [];
    this.bumps = [];
    this.caps = [];
    this.plats = [];
    this.boss = null;
    this.bossDead = false;
    this.clearT = 0;
    this.flagT = 0;
    this.warpT = 0;
    this.pendingWarp = null;

    for (var i = 0; i < lv.plats.length; i++) {
      var p = lv.plats[i];
      var pe = new Ent('plat', p.x * T, p.y * T, p.w * T, 8);
      pe.axis = p.axis; pe.range = p.range * T; pe.speed = p.speed;
      pe.baseX = pe.x; pe.baseY = pe.y; pe.phase = i * 1.1; pe.dx = 0; pe.dy = 0;
      this.plats.push(pe);
    }

    var sp = spawnAt || lv.spawn;
    var pl = new Ent('sal', sp.x * T, sp.y * T, 10, 14);
    pl.big = false; pl.fire = false; pl.star = 0; pl.inv = 0;
    pl.anim = 0; pl.ducking = false; pl.grow = 0; pl.riding = null;
    if (keepStats && this.player) {
      pl.big = this.player.big; pl.fire = this.player.fire;
      if (pl.big) { pl.w = 12; pl.h = 24; pl.y = sp.y * T - 10; }
    }
    this.player = pl;
    this.centerCamera();
    this.mode = 'play';
    this.msg = { text: 'WORLD ' + lv.world + '  ' + lv.name, t: 150 };
    SND.play(lv.music);
    return lv;
  };

  Game.prototype.centerCamera = function () {
    var maxX = this.level.w * T - VW;
    this.cam.x = Math.max(0, Math.min(maxX, this.player.x - VW * 0.42));
  };

  /* — the tile grid — */

  Game.prototype.tileAt = function (tx, ty) {
    var lv = this.level;
    if (ty < 0 || ty >= lv.h) return ' ';
    if (tx < 0) return 'S';                 // the left edge is a wall
    if (tx >= lv.w) return ' ';
    return lv.grid[ty][tx];
  };

  Game.prototype.solidAt = function (tx, ty) {
    return !!SOLID[this.tileAt(tx, ty)];
  };

  Game.prototype.setTile = function (tx, ty, ch) {
    if (ty < 0 || ty >= this.level.h || tx < 0 || tx >= this.level.w) return;
    this.level.grid[ty][tx] = ch;
  };

  /* — collision — */

  Game.prototype.moveX = function (e) {
    e.x += e.vx;
    var y0 = Math.floor(e.y / T), y1 = Math.floor((e.y + e.h - 1) / T), ty, tx;
    if (e.vx > 0) {
      tx = Math.floor((e.x + e.w - 1) / T);
      for (ty = y0; ty <= y1; ty++) {
        if (this.solidAt(tx, ty)) {
          e.x = tx * T - e.w; e.vx = 0; e.hitWall = 1; return;
        }
      }
    } else if (e.vx < 0) {
      tx = Math.floor(e.x / T);
      for (ty = y0; ty <= y1; ty++) {
        if (this.solidAt(tx, ty)) {
          e.x = (tx + 1) * T; e.vx = 0; e.hitWall = -1; return;
        }
      }
    }
  };

  Game.prototype.moveY = function (e) {
    e.y += e.vy;
    var x0 = Math.floor(e.x / T), x1 = Math.floor((e.x + e.w - 1) / T), tx, ty;
    e.onGround = false;
    if (e.vy > 0) {
      ty = Math.floor((e.y + e.h - 1) / T);
      for (tx = x0; tx <= x1; tx++) {
        if (this.solidAt(tx, ty)) {
          e.y = ty * T - e.h; e.vy = 0; e.onGround = true; return;
        }
      }
    } else if (e.vy < 0) {
      ty = Math.floor(e.y / T);
      var best = null, bestD = 1e9;
      for (tx = x0; tx <= x1; tx++) {
        if (this.solidAt(tx, ty)) {
          var d = Math.abs((tx * T + T / 2) - (e.x + e.w / 2));
          if (d < bestD) { bestD = d; best = tx; }
        }
      }
      if (best !== null) {
        e.y = (ty + 1) * T; e.vy = 0;
        e.headTile = { x: best, y: ty };
      }
    }
  };

  /* — spawning — */

  Game.prototype.spawnAhead = function () {
    var lv = this.level, edge = this.cam.x + VW + 48;
    for (var i = 0; i < lv.foes.length; i++) {
      var d = lv.foes[i];
      if (d.spawned || d.x * T > edge) continue;
      d.spawned = true;
      this.ents.push(this.makeFoe(d));
    }
    if (lv.bossAt && !this.boss && !this.bossDead &&
        this.player.x > (lv.bossAt.x - 11) * T) {
      var b = new Ent('boss', lv.bossAt.x * T, lv.bossAt.y * T - 16, 28, 30);
      b.hp = 5; b.dir = -1; b.cool = 90; b.hurt = 0;
      this.boss = b; this.ents.push(b);
      SND.sfx('boss');
      this.msg = { text: 'THE LANDLORD WANTS A WORD', t: 130 };
      this.shake = 16;
    }
  };

  Game.prototype.makeFoe = function (d) {
    var e;
    switch (d.kind) {
      case 'pigeon':
        e = new Ent('pigeon', d.x * T, d.y * T, 14, 14);
        e.vx = -0.35; e.dir = -1; break;
      case 'rat':
        e = new Ent('rat', d.x * T, d.y * T, 15, 12);
        e.y += 4; e.vx = -0.6; e.dir = -1; break;
      case 'gull':
        e = new Ent('gull', d.x * T, d.y * T, 14, 10);
        e.baseY = e.y; e.vx = -0.5; e.dir = -1; e.fly = true; break;
      default:
        e = new Ent('token', d.x * T, d.y * T, 16, 16);
        e.pickup = true; break;
    }
    return e;
  };

  /* — block contents — */

  Game.prototype.bumpBlock = function (tx, ty) {
    var ch = this.tileAt(tx, ty);
    if (ch !== '?' && ch !== 'B') return;
    var key = tx + ',' + ty;
    var content = this.level.contents[key];
    var p = this.player;

    if (ch === 'B' && !content) {
      if (p.big) {
        this.setTile(tx, ty, ' ');
        this.debris(tx * T + 8, ty * T + 8);
        this.score += 50;
        SND.sfx('brick');
      } else {
        this.bumps.push({ x: tx, y: ty, t: 0 });
        SND.sfx('bump');
      }
      this.killAbove(tx, ty);
      return;
    }

    if (!content) { SND.sfx('bump'); this.bumps.push({ x: tx, y: ty, t: 0 }); return; }

    this.bumps.push({ x: tx, y: ty, t: 0 });
    this.killAbove(tx, ty);

    if (content === 'multi') {
      if (this.level.multi === undefined) this.level.multi = {};
      var m = this.level.multi[key];
      if (m === undefined) { m = this.level.multi[key] = { n: 6, t: 0 }; }
      m.n--;
      this.collectToken(tx * T + 8, ty * T);
      if (m.n <= 0) { this.setTile(tx, ty, 'U'); delete this.level.contents[key]; }
      return;
    }

    delete this.level.contents[key];
    this.setTile(tx, ty, 'U');

    if (content === 'coin') {
      this.collectToken(tx * T + 8, ty * T);
      return;
    }

    var item;
    if (content === 'pizza') {
      item = new Ent(p.big || p.fire ? 'eggcream' : 'pizza', tx * T, ty * T, 16, 16);
    } else if (content === 'star') {
      item = new Ent('metrocard', tx * T, ty * T, 16, 16);
    } else if (content === '1up') {
      item = new Ent('sandwich', tx * T, ty * T, 16, 16);
    } else {
      item = new Ent('pizza', tx * T, ty * T, 16, 16);
    }
    item.sprouting = 16;
    item.pickup = true;
    item.vx = (item.kind === 'pizza' || item.kind === 'metrocard') ? 0.7 : 0;
    this.ents.push(item);
    SND.sfx('sprout');
  };

  /* Anything standing on a block that just got hit goes flying. */
  Game.prototype.killAbove = function (tx, ty) {
    for (var i = 0; i < this.ents.length; i++) {
      var e = this.ents[i];
      if (e.kind === 'token' || e.pickup || e.dead || e.kind === 'boss') continue;
      if (e.y + e.h > ty * T - 4 && e.y + e.h < ty * T + 8 &&
          e.x + e.w > tx * T && e.x < tx * T + T) {
        this.killFoe(e, true);
      }
    }
  };

  Game.prototype.collectToken = function (x, y) {
    this.tokens++;
    this.score += 200;
    if (this.tokens >= 100) { this.tokens -= 100; this.addLife(); }
    var t = new Ent('spark', x - 8, y - 16, 16, 16);
    t.vy = -4.6; t.life = 34; t.token = true;
    this.parts.push(t);
    SND.sfx('coin');
  };

  Game.prototype.addLife = function () {
    this.lives++;
    SND.sfx('oneup');
    this.pops.push({ x: this.player.x, y: this.player.y - 8, text: '1UP', t: 60 });
  };

  Game.prototype.debris = function (x, y) {
    for (var i = 0; i < 4; i++) {
      var p = new Ent('debris', x, y, 6, 6);
      p.vx = (i % 2 ? 1 : -1) * (1 + Math.random());
      p.vy = -3 - (i < 2 ? 1.2 : 0);
      p.life = 70;
      this.parts.push(p);
    }
  };

  Game.prototype.pop = function (x, y, s) {
    this.pops.push({ x: x, y: y, text: s, t: 50 });
  };

  /* ── update ────────────────────────────────────────────────────────── */

  // One simulated step. Input ages with the step rather than with the frame
  // loop, so a test driving update() by hand sees the same input as a player.
  Game.prototype.update = function () {
    this.step();
    ageInput();
  };

  Game.prototype.step = function () {
    this.frame++;
    if (this.shake > 0) this.shake--;

    if (this.mode === 'title') return this.updateTitle();
    if (this.mode === 'gameover') {
      if (--this.overT <= 0 || consume('start')) { this.toTitle(); }
      return;
    }

    if (consume('pause') && (this.mode === 'play')) {
      this.paused = !this.paused;
      SND.sfx('pause');
      if (this.paused) SND.stop(); else SND.play(this.level.music);
    }
    if (this.paused) return;

    if (this.msg && --this.msg.t <= 0) this.msg = null;

    if (this.mode === 'warp') return this.updateWarp();
    if (this.mode === 'clear') return this.updateClear();

    this.spawnAhead();
    this.updatePlats();

    if (this.mode === 'dying') {
      var p = this.player;
      p.deadT++;
      if (p.deadT > 24) { p.vy += 0.4; p.y += p.vy; }
      if (p.deadT > 170) this.respawn();
      return;
    }

    this.updatePlayer();
    this.updateEnts();
    this.updateCaps();
    this.updateParts();

    for (var i = 0; i < this.bumps.length; i++)
      if (++this.bumps[i].t > 12) this.bumps.splice(i--, 1);
    for (var j = 0; j < this.pops.length; j++) {
      this.pops[j].y -= 0.5;
      if (--this.pops[j].t <= 0) this.pops.splice(j--, 1);
    }

    // the clock
    if (++this.timeSub >= 24) {
      this.timeSub = 0;
      this.time--;
      if (this.time === 100) SND.sfx('select');
      if (this.time <= 0) { this.time = 0; this.die(); }
    }

    this.centerCameraFollow();
  };

  Game.prototype.centerCameraFollow = function () {
    var maxX = this.level.w * T - VW;
    var want = this.player.x - VW * 0.42;
    if (want > this.cam.x) this.cam.x = Math.min(maxX, want);
    this.cam.x = Math.max(0, Math.min(maxX, this.cam.x));
    if (this.player.x < this.cam.x + 2) { this.player.x = this.cam.x + 2; this.player.vx = 0; }
  };

  Game.prototype.updateTitle = function () {
    this.titleT++;
    if (consume('start') || consume('jump')) {
      SND.sfx('select');
      this.score = 0; this.tokens = 0; this.lives = 3;
      this.player = null;
      clearEdges();          // don't carry the start press into the first step
      this.loadLevel(LEVELS.order[0]);
    }
    if (consume('mute')) SND.toggle();
  };

  Game.prototype.toTitle = function () {
    this.mode = 'title';
    this.titleT = 0;
    SND.stop();
  };

  /* — moving planks — */

  Game.prototype.updatePlats = function () {
    for (var i = 0; i < this.plats.length; i++) {
      var p = this.plats[i];
      p.phase += p.speed * 0.03;
      var o = Math.sin(p.phase) * p.range;
      var nx = p.baseX, ny = p.baseY;
      if (p.axis === 'x') nx = p.baseX + o; else ny = p.baseY + o;
      p.dx = nx - p.x; p.dy = ny - p.y;
      p.x = nx; p.y = ny;
    }
  };

  Game.prototype.ridePlat = function (e) {
    var landed = null;
    for (var i = 0; i < this.plats.length; i++) {
      var p = this.plats[i];
      if (e.x + e.w <= p.x + 1 || e.x >= p.x + p.w - 1) continue;
      var feet = e.y + e.h;
      if (e.vy >= 0 && feet >= p.y && feet <= p.y + 10 + Math.abs(p.dy)) {
        e.y = p.y - e.h;
        e.vy = 0;
        e.onGround = true;
        landed = p;
        break;
      }
    }
    if (landed) { e.x += landed.dx; e.y += landed.dy; }
    e.riding = landed;
  };

  /* — Sal — */

  Game.prototype.updatePlayer = function () {
    var p = this.player, lv = this.level;

    if (p.grow > 0) { p.grow--; return; }        // freeze during grow/shrink

    var left = keys.left, right = keys.right, run = keys.run;
    var acc = run ? 0.16 : 0.10;
    var max = run ? 2.35 : 1.35;

    p.ducking = false;
    if (p.big && keys.down && p.onGround) {
      p.ducking = true;
      left = right = false;
    }

    if (left && !right) {
      p.vx -= acc; p.dir = -1;
      if (p.vx > 0) p.vx -= 0.14;
    } else if (right && !left) {
      p.vx += acc; p.dir = 1;
      if (p.vx < 0) p.vx += 0.14;
    } else if (p.onGround) {
      if (Math.abs(p.vx) < 0.09) p.vx = 0;
      else p.vx -= Math.sign(p.vx) * 0.09;
    }
    p.vx = Math.max(-max, Math.min(max, p.vx));

    // jump — the coyote frames banked on landing keep a jump asked for just
    // after walking off an edge, and the ground test comes first so a buffered
    // press is not spent on a frame that cannot use it.
    if (!p.onGround && p.coyote > 0) p.coyote--;

    if ((p.onGround || p.coyote > 0) && consume('jump')) {
      p.vy = -4.9 - Math.abs(p.vx) * 0.32;
      p.jumping = true;
      p.onGround = false;
      p.coyote = 0;
      SND.sfx(p.big ? 'bigjump' : 'jump');
    }
    if (!keys.jump) p.jumping = false;
    p.vy += (p.jumping && p.vy < 0) ? 0.20 : 0.40;
    p.vy = Math.min(p.vy, 7.2);

    p.hitWall = 0;
    p.headTile = null;
    this.moveX(p);
    this.moveY(p);
    this.ridePlat(p);
    if (p.onGround) p.coyote = 6;               // banked the moment he lands

    if (p.headTile) this.bumpBlock(p.headTile.x, p.headTile.y);

    if (p.star > 0) {
      p.star--;
      if (p.star === 0 && SND.current() === 'star') SND.play(lv.music);
    }
    if (p.inv > 0) p.inv--;

    // walked into the void
    if (p.y > VH + 32) { this.die(); return; }

    // pipes down to the cellar, and the way back out
    if (p.onGround && keys.down) this.checkWarp();

    // the finish line
    if (lv.goalX !== null && p.x + p.w > lv.goalX * T + 4 && this.mode === 'play') {
      this.startClear();
      return;
    }

    // animation
    var sp = Math.abs(p.vx);
    p.anim += sp * 0.28 + (sp > 0.05 ? 0.05 : 0);
  };

  Game.prototype.checkWarp = function () {
    var p = this.player, lv = this.level, i;
    for (i = 0; i < lv.warps.length; i++) {
      var w = lv.warps[i];
      if (p.x + p.w > w.x * T + 2 && p.x < (w.x + w.w) * T - 2 &&
          Math.abs((p.y + p.h) - w.y * T) < 6) {
        this.beginWarp(w.to, w.at);
        return;
      }
    }
    if (lv.exitPipe) {
      var e = lv.exitPipe;
      if (p.x + p.w > e.x * T + 2 && p.x < (e.x + e.w) * T - 2 &&
          Math.abs((p.y + p.h) - e.y * T) < 6) {
        this.beginWarp(lv.exit.to, lv.exit.at);
      }
    }
  };

  Game.prototype.beginWarp = function (to, at) {
    this.mode = 'warp';
    this.warpT = 0;
    this.pendingWarp = { to: to, at: at };
    SND.sfx('pipe');
    SND.stop();
  };

  Game.prototype.updateWarp = function () {
    this.warpT++;
    this.player.y += 0.9;
    if (this.warpT > 46) {
      var w = this.pendingWarp;
      this.loadLevel(w.to, w.at, true);
    }
  };

  /* — damage, death, power — */

  Game.prototype.hurt = function () {
    var p = this.player;
    if (p.inv > 0 || p.star > 0 || this.mode !== 'play') return;
    if (p.fire) {
      p.fire = false; p.inv = 110; p.grow = 22;
      SND.sfx('hurt');
    } else if (p.big) {
      p.big = false;
      p.y += 10; p.w = 10; p.h = 14;
      p.inv = 110; p.grow = 22;
      SND.sfx('hurt');
    } else {
      this.die();
    }
  };

  Game.prototype.die = function () {
    if (this.mode !== 'play') return;
    this.mode = 'dying';
    var p = this.player;
    p.deadT = 0; p.vy = -5.4; p.vx = 0; p.dead = true;
    SND.stop();
    SND.sfx('onedown');
  };

  Game.prototype.respawn = function () {
    this.lives--;
    if (this.lives <= 0) {
      this.mode = 'gameover';
      this.overT = 210;
      if (this.score > this.high) {
        this.high = this.score;
        try { localStorage.setItem('superhood.high', String(this.high)); } catch (e) {}
      }
      SND.play('gameover');
      return;
    }
    // dying in the cellar puts you back on the avenue, not back in the cellar
    var id = this.levelId === 'cellar' ? '1-1' : this.levelId;
    this.player = null;
    this.loadLevel(id, null, false);
  };

  Game.prototype.grow = function () {
    var p = this.player;
    if (!p.big) {
      p.big = true;
      p.w = 12; p.h = 24; p.y -= 10;
      p.grow = 26;
    }
    this.score += 1000;
    SND.sfx('powerup');
  };

  /* — enemies and items — */

  Game.prototype.updateEnts = function () {
    var p = this.player;
    for (var i = 0; i < this.ents.length; i++) {
      var e = this.ents[i];
      e.t++;

      if (e.remove || e.x < this.cam.x - 96 || e.y > VH + 64) {
        if (e.kind === 'boss') { this.boss = null; }
        this.ents.splice(i--, 1);
        continue;
      }

      if (e.kind === 'token') {
        if (e.overlaps(p)) { this.collectToken(e.x + 8, e.y); e.remove = true; }
        continue;
      }

      if (e.sprouting > 0) {                    // rising out of a crate
        e.sprouting--;
        e.y -= 1;
        continue;
      }

      if (e.dead) {                             // squashed / flipped
        if (e.flip) { e.vy += 0.4; e.y += e.vy; e.x += e.vx; }
        else if (--e.deadT <= 0) e.remove = true;
        continue;
      }

      switch (e.kind) {
        case 'pigeon':
          e.vy += 0.4;
          e.hitWall = 0;
          this.moveX(e);
          if (e.hitWall) e.vx = -e.vx;
          this.moveY(e);
          this.ridePlat(e);
          if (e.vx === 0) e.vx = (e.dir = -e.dir) * 0.35;
          e.dir = e.vx < 0 ? -1 : 1;
          break;

        case 'rat':
          if (e.state === 'lid') {
            e.vy += 0.4;
            e.hitWall = 0;
            this.moveX(e);
            if (e.hitWall) { e.vx = -e.vx; SND.sfx('bump'); }
            this.moveY(e);
            if (e.vx !== 0) this.lidHits(e);
          } else {
            e.vy += 0.4;
            e.hitWall = 0;
            this.moveX(e);
            if (e.hitWall) e.vx = -e.vx;
            this.moveY(e);
            this.ridePlat(e);
            e.dir = e.vx < 0 ? -1 : 1;
          }
          break;

        case 'gull':
          e.x += e.vx;
          e.y = e.baseY + Math.sin(e.t * 0.05) * 26;
          if (e.x < this.cam.x - 40) e.remove = true;
          e.dir = e.vx < 0 ? -1 : 1;
          break;

        case 'boss':
          this.updateBoss(e);
          break;

        case 'notice':
          e.vy += 0.14;
          e.x += e.vx; e.y += e.vy;
          if (e.y > VH) e.remove = true;
          if (e.overlaps(p)) { e.remove = true; this.hurt(); }
          continue;

        case 'pizza': case 'eggcream': case 'metrocard': case 'sandwich':
          if (e.kind === 'pizza' || e.kind === 'metrocard') {
            e.vy += 0.35;
            e.hitWall = 0;
            this.moveX(e);
            if (e.hitWall) e.vx = -e.vx;
            this.moveY(e);
            this.ridePlat(e);
            if (e.kind === 'metrocard' && e.onGround) e.vy = -3.6;
          }
          if (e.overlaps(p)) {
            e.remove = true;
            if (e.kind === 'pizza') { this.grow(); this.pop(e.x, e.y, '1000'); }
            else if (e.kind === 'eggcream') {
              if (!p.big) this.grow(); else { this.score += 1000; SND.sfx('powerup'); }
              p.fire = true;
              this.pop(e.x, e.y, '1000');
            } else if (e.kind === 'metrocard') {
              p.star = 620; this.score += 1000;
              this.pop(e.x, e.y, '1000');
              SND.sfx('powerup'); SND.play('star');
            } else { this.addLife(); }
          }
          continue;
      }

      if (e.kind === 'boss') continue;

      // enemy vs Sal
      if (this.mode === 'play' && !e.dead && e.overlaps(p)) {
        if (p.star > 0) {
          this.killFoe(e, true);
          this.score += 200; this.pop(e.x, e.y, '200');
        } else if (e.kind === 'rat' && e.state === 'lid' && e.vx === 0 &&
                   p.y + p.h < e.y + 14) {
          this.stompOn(p);
          e.vx = (p.x + p.w / 2 < e.x + e.w / 2) ? 3.1 : -3.1;
          SND.sfx('kick');
        } else if (p.vy > 0 && (p.y + p.h) - p.vy <= e.y + 8) {
          this.stomp(e);
        } else if (e.kind === 'rat' && e.state === 'lid' && e.vx === 0) {
          e.vx = (p.x + p.w / 2 < e.x + e.w / 2) ? 3.1 : -3.1;
          SND.sfx('kick');
        } else {
          this.hurt();
        }
      }
    }
  };

  Game.prototype.stompOn = function (p) {
    p.vy = keys.jump ? -5.2 : -3.4;
  };

  Game.prototype.stomp = function (e) {
    var p = this.player;
    this.stompOn(p);
    if (e.kind === 'rat' && e.state !== 'lid') {
      e.state = 'lid'; e.vx = 0; e.h = 11; e.y += 1; e.w = 14;
      this.score += 100; this.pop(e.x, e.y, '100');
      SND.sfx('stomp');
      return;
    }
    if (e.kind === 'rat' && e.state === 'lid') {
      e.vx = 0;
      SND.sfx('stomp');
      return;
    }
    e.dead = true; e.deadT = 26; e.vx = 0;
    this.score += 100;
    this.pop(e.x, e.y, '100');
    SND.sfx('stomp');
  };

  Game.prototype.killFoe = function (e, flip) {
    if (e.kind === 'boss') return;
    e.dead = true; e.flip = !!flip; e.deadT = 26;
    e.vy = -4; e.vx = 0.6 * (Math.random() < 0.5 ? -1 : 1);
    this.score += 100;
    SND.sfx('stomp');
  };

  /* A sliding trash-can lid clears everything in its lane. */
  Game.prototype.lidHits = function (lid) {
    for (var i = 0; i < this.ents.length; i++) {
      var e = this.ents[i];
      if (e === lid || e.dead || e.pickup || e.kind === 'token') continue;
      if (e.kind === 'boss') { this.hitBoss(e); continue; }
      if (lid.overlaps(e)) {
        this.killFoe(e, true);
        this.score += 200;
        this.pop(e.x, e.y, '200');
      }
    }
  };

  /* — the boss — */

  Game.prototype.updateBoss = function (b) {
    var p = this.player;
    if (b.hurt > 0) b.hurt--;
    b.dir = (p.x < b.x) ? -1 : 1;

    b.vy += 0.34;
    if (b.onGround && b.t % 46 === 0) {
      b.vy = -4.6;
      b.vx = b.dir * 0.9;
    }
    if (b.onGround) b.vx *= 0.86;
    b.hitWall = 0;
    this.moveX(b);
    if (b.hitWall) b.vx = 0;
    this.moveY(b);
    if (b.onGround && b.landed !== true) { b.landed = true; this.shake = 6; }
    if (!b.onGround) b.landed = false;

    if (--b.cool <= 0) {
      b.cool = 118;
      var n = new Ent('notice', b.x + 12, b.y + 6, 10, 10);
      n.vx = b.dir * 1.5; n.vy = -2.4;
      this.ents.push(n);
      SND.sfx('fire');
    }

    if (this.mode !== 'play') return;
    if (b.overlaps(p)) {
      if (p.star > 0) { this.hitBoss(b); this.hitBoss(b); }
      else if (p.vy > 0 && (p.y + p.h) - p.vy <= b.y + 12) {
        this.stompOn(p);
        this.hitBoss(b);
      } else if (b.hurt <= 0) {
        this.hurt();
      }
    }
  };

  Game.prototype.hitBoss = function (b) {
    if (b.hurt > 0 || b.dead) return;
    b.hp--;
    b.hurt = 40;
    this.shake = 8;
    this.score += 500;
    this.pop(b.x + 6, b.y, '500');
    SND.sfx('stomp');
    if (b.hp <= 0) {
      b.dead = true; b.flip = true; b.vy = -6; b.vx = -1.4; b.deadT = 200;
      this.bossDead = true;
      this.score += 5000;
      this.pop(b.x, b.y - 8, '5000');
      this.shake = 24;
      SND.sfx('boss');
      var gt = this.level.gate;
      if (gt) {
        for (var y = gt.y0; y <= gt.y1; y++) {
          this.setTile(gt.x, y, ' ');
          this.debris(gt.x * T + 8, y * T + 8);
        }
      }
      this.msg = { text: 'THE BLOCK IS YOURS', t: 160 };
    }
  };

  /* — bottle caps — */

  Game.prototype.throwCap = function () {
    var p = this.player;
    if (!p.fire || this.caps.length >= 2 || this.mode !== 'play') return;
    var c = new Ent('cap', p.x + (p.dir > 0 ? p.w : -6), p.y + 6, 6, 6);
    c.vx = p.dir * 3.4; c.vy = 1.2; c.life = 220;
    this.caps.push(c);
    SND.sfx('cap');
  };

  Game.prototype.updateCaps = function () {
    for (var i = 0; i < this.caps.length; i++) {
      var c = this.caps[i];
      c.t++;
      c.vy += 0.32;
      c.hitWall = 0;
      this.moveX(c);
      if (c.hitWall) { this.caps.splice(i--, 1); continue; }
      this.moveY(c);
      if (c.onGround) c.vy = -3.1;
      if (--c.life <= 0 || c.x < this.cam.x - 16 || c.x > this.cam.x + VW + 16 ||
          c.y > VH + 16) {
        this.caps.splice(i--, 1); continue;
      }
      for (var j = 0; j < this.ents.length; j++) {
        var e = this.ents[j];
        if (e.dead || e.pickup || e.kind === 'token' || e.kind === 'notice') continue;
        if (c.overlaps(e)) {
          if (e.kind === 'boss') this.hitBoss(e);
          else { this.killFoe(e, true); this.score += 200; this.pop(e.x, e.y, '200'); }
          this.caps.splice(i--, 1);
          break;
        }
      }
    }
  };

  Game.prototype.updateParts = function () {
    for (var i = 0; i < this.parts.length; i++) {
      var p = this.parts[i];
      p.t++;
      if (p.token) { p.vy += 0.34; p.y += p.vy; }
      else { p.vy += 0.4; p.x += p.vx; p.y += p.vy; }
      if (--p.life <= 0) this.parts.splice(i--, 1);
    }
  };

  /* — the finish — */

  Game.prototype.startClear = function () {
    this.mode = 'clear';
    this.clearT = 0;
    this.flagY = 0;
    var p = this.player;
    p.vx = 0; p.vy = 0;
    p.x = this.level.goalX * T - 6;
    SND.stop();
    SND.sfx('flag');
  };

  Game.prototype.updateClear = function () {
    var p = this.player, lv = this.level;
    this.clearT++;
    var groundY = 11 * T + (16 - p.h) + T;

    if (this.clearT < 60) {
      this.flagY = Math.min(1, this.clearT / 55);
      if (p.y + p.h < 12 * T) p.y += 2.2;
    } else if (this.clearT === 60) {
      SND.play('clear');
      this.score += this.time * 50;
      this.time = 0;
    } else if (this.clearT < 118) {
      p.x += 1.1;
      p.dir = 1;
      p.vy += 0.4;
      this.moveY(p);
      p.anim += 0.3;
    } else if (this.clearT === 130) {
      this.msg = { text: 'COURSE CLEAR', t: 110 };
    } else if (this.clearT > 250) {
      var idx = LEVELS.order.indexOf(this.levelId);
      if (idx >= 0 && idx + 1 < LEVELS.order.length) {
        this.loadLevel(LEVELS.order[idx + 1], null, true);
      } else {
        this.mode = 'gameover';
        this.won = true;
        this.overT = 400;
        if (this.score > this.high) {
          this.high = this.score;
          try { localStorage.setItem('superhood.high', String(this.high)); } catch (e) {}
        }
        SND.play('clear');
      }
    }
    void groundY; void lv;
  };

  /* ── render ────────────────────────────────────────────────────────── */

  Game.prototype.render = function () {
    var g = this.g;
    g.save();
    if (this.shake > 0) {
      g.translate((Math.random() - 0.5) * this.shake * 0.6,
                  (Math.random() - 0.5) * this.shake * 0.6);
    }

    if (this.mode === 'title') { this.renderTitle(g); g.restore(); return; }

    this.renderBackground(g);
    this.renderTiles(g);
    this.renderDecor(g);
    this.renderGoal(g);
    this.renderEnts(g);
    this.renderPlayer(g);
    this.renderParts(g);
    this.renderHUD(g);

    if (this.mode === 'gameover') this.renderGameOver(g);
    if (this.paused) {
      g.fillStyle = 'rgba(0,0,0,0.55)';
      g.fillRect(0, 0, VW, VH);
      textCentered(g, 'PAUSED', 108, '#ffffff', 2);
      textCentered(g, 'P TO RESUME', 136, '#f7d51d', 1);
    }
    g.restore();
  };

  Game.prototype.renderBackground = function (g) {
    var th = THEME[this.theme], cam = this.cam.x;
    var grd = g.createLinearGradient(0, 0, 0, VH);
    grd.addColorStop(0, th.sky[0]);
    grd.addColorStop(1, th.sky[1]);
    g.fillStyle = grd;
    g.fillRect(0, 0, VW, VH);

    if (this.theme === 'tunnel') {
      // work lights strung along the tunnel
      var lx = -((cam * 0.6) % 64);
      for (var i = 0; i < 6; i++) {
        var x = lx + i * 64;
        var r = g.createRadialGradient(x, 34, 2, x, 34, 46);
        r.addColorStop(0, 'rgba(255,220,120,0.30)');
        r.addColorStop(1, 'rgba(255,220,120,0)');
        g.fillStyle = r;
        g.fillRect(x - 46, 0, 92, 92);
        g.fillStyle = '#f7d51d';
        g.fillRect(x - 1, 30, 3, 3);
      }
      return;
    }

    // the skyline, two parallax layers deep
    this.renderSkyline(g, cam * 0.18, 0.55, '#7f8fbf', 96);
    this.renderSkyline(g, cam * 0.34, 1.0, '#5a6a9a', 118);
    this.renderClouds(g, cam * 0.10);

    if (this.theme === 'park') {
      // the Williamsburgh Bank tower, the thing you can see from everywhere
      var bx = 200 - (cam * 0.18) % 420;
      for (var k = 0; k < 3; k++) this.bankTower(g, bx + k * 420, 34);
    }
  };

  Game.prototype.bankTower = function (g, x, y) {
    g.fillStyle = '#6b6f9c';
    g.fillRect(x, y, 22, 84);
    g.fillStyle = '#565a80';
    g.fillRect(x + 2, y + 12, 18, 2);
    g.fillStyle = '#8b8fbe';
    g.fillRect(x + 4, y - 10, 14, 12);
    g.fillStyle = '#c8ccf0';
    g.fillRect(x + 7, y - 7, 8, 8);
    g.fillStyle = '#3a3d5c';
    g.fillRect(x + 10, y - 6, 2, 4);
    g.fillRect(x + 10, y - 4, 4, 2);
    g.fillStyle = '#8b8fbe';
    g.fillRect(x + 9, y - 20, 4, 10);
  };

  /* The skyline is baked once into a 512px strip and then tiled, so it scrolls
   * seamlessly instead of re-rolling its own buildings every frame. */
  var skyCache = {};
  function skylineStrip(sat, color, baseY) {
    var key = sat + color + baseY;
    if (skyCache[key]) return skyCache[key];
    var W = 512, HH = VH;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = HH;
    var g = cv.getContext('2d');
    var seed = 1337 + Math.round(sat * 977) + baseY;
    function rnd() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }
    var x = 0;
    while (x < W) {
      var w = 18 + Math.floor(rnd() * 26);
      if (x + w > W) w = W - x;                  // last one squares off the loop
      var h = 26 + Math.floor(rnd() * 62 * sat);
      var y = baseY + 74 - h;
      g.fillStyle = color;
      g.fillRect(x, y, w, HH - y);
      if (rnd() > 0.66 && w > 12) {              // a water tower on the roof
        var tw = 8, tx = x + Math.floor(w / 2) - 4;
        g.fillStyle = '#4a3a2a';
        g.fillRect(tx, y - 11, tw, 8);
        g.fillRect(tx + 1, y - 3, 1, 3);
        g.fillRect(tx + 6, y - 3, 1, 3);
        g.fillStyle = '#5c4a34';
        g.fillRect(tx - 1, y - 13, tw + 2, 2);
      }
      g.fillStyle = 'rgba(255,240,180,0.22)';    // somebody is always awake
      for (var wy = y + 5; wy < y + h - 3; wy += 7)
        for (var wx = x + 3; wx < x + w - 4; wx += 6)
          if (rnd() > 0.55) g.fillRect(wx, wy, 3, 4);
      x += w + 2 + Math.floor(rnd() * 5);
    }
    skyCache[key] = cv;
    return cv;
  }

  Game.prototype.renderSkyline = function (g, off, sat, color, baseY) {
    var strip = skylineStrip(sat, color, baseY);
    var x = -(((off % 512) + 512) % 512);
    while (x < VW) { g.drawImage(strip, Math.round(x), 0); x += 512; }
  };

  Game.prototype.renderClouds = function (g, off) {
    var seeds = [40, 150, 260, 380, 470];
    g.fillStyle = 'rgba(255,255,255,0.85)';
    for (var i = 0; i < seeds.length; i++) {
      var x = ((seeds[i] - off) % 560 + 560) % 560 - 60;
      var y = 18 + (i % 3) * 16;
      g.fillRect(x + 6, y, 20, 6);
      g.fillRect(x, y + 5, 34, 7);
      g.fillRect(x + 12, y - 4, 12, 5);
    }
  };

  Game.prototype.renderTiles = function (g) {
    var lv = this.level, cam = Math.floor(this.cam.x);
    var x0 = Math.floor(cam / T), x1 = x0 + Math.ceil(VW / T) + 1;
    var frame = Math.floor(this.frame / 10) % 4;
    for (var tx = x0; tx <= x1; tx++) {
      for (var ty = 0; ty < lv.h; ty++) {
        var ch = this.tileAt(tx, ty);
        if (ch === ' ') continue;
        var cv = tile(ch, this.theme, frame);
        if (!cv) continue;
        var oy = 0;
        for (var b = 0; b < this.bumps.length; b++) {
          var bm = this.bumps[b];
          if (bm.x === tx && bm.y === ty) {
            oy = -Math.round(Math.sin((bm.t / 12) * Math.PI) * 7);
          }
        }
        g.drawImage(cv, tx * T - cam, ty * T + oy);
      }
    }
  };

  Game.prototype.renderDecor = function (g) {
    var lv = this.level, cam = Math.floor(this.cam.x);
    for (var i = 0; i < lv.decor.length; i++) {
      var d = lv.decor[i];
      var x = d.x * T - cam;
      if (x < -48 || x > VW + 48) continue;
      if (d.kind === 'tree') g.drawImage(SPR.tree, x - 8, (d.y + 1) * T - 32, 32, 32);
      else if (d.kind === 'bush') g.drawImage(SPR.bush, x, d.y * T);
      else if (d.kind === 'hydrant') g.drawImage(SPR.hydrant, x, d.y * T);
      else if (d.kind === 'trash') g.drawImage(SPR.trash, x, d.y * T);
    }
    // moving planks
    for (var j = 0; j < this.plats.length; j++) {
      var p = this.plats[j];
      var px = Math.round(p.x - cam);
      if (px < -64 || px > VW + 64) continue;
      g.fillStyle = '#8c8c98';
      g.fillRect(px, Math.round(p.y), p.w, 8);
      g.fillStyle = '#c8c8d4';
      g.fillRect(px, Math.round(p.y), p.w, 2);
      g.fillStyle = '#5a5a66';
      g.fillRect(px, Math.round(p.y) + 6, p.w, 2);
      for (var k = 4; k < p.w; k += 12) {
        g.fillStyle = '#4a4a54';
        g.fillRect(px + k, Math.round(p.y) + 3, 2, 2);
      }
    }
  };

  Game.prototype.renderGoal = function (g) {
    var lv = this.level;
    if (lv.goalX === null) return;
    var cam = Math.floor(this.cam.x);
    var x = lv.goalX * T - cam;
    if (x < -80 || x > VW + 120) return;
    var top = lv.goalTop * T, bottom = 12 * T;

    // the lamppost
    g.fillStyle = '#2e2e38';
    g.fillRect(x + 6, top, 4, bottom - top);
    g.fillRect(x, bottom - 4, 16, 4);
    g.fillStyle = '#f7d51d';
    g.fillRect(x + 2, top - 6, 12, 6);
    g.fillStyle = '#fff6c0';
    g.fillRect(x + 4, top - 4, 8, 3);

    // the pennant, which rides down as you slide
    var fy = top + 8 + (this.mode === 'clear' ? (this.flagY || 0) * (bottom - top - 30) : 0);
    g.fillStyle = '#1a3fd0';
    g.fillRect(x + 10, fy, 20, 12);
    g.fillStyle = '#ffffff';
    text(g, 'B', x + 16, fy + 3, '#ffffff', 1);

    // the brownstone you walk home into
    var hx = x + 34, s;
    g.fillStyle = '#8a4a28';                       // facade
    g.fillRect(hx, bottom - 68, 60, 68);
    g.fillStyle = '#6c381c';                       // cornice
    g.fillRect(hx - 2, bottom - 72, 64, 5);
    g.fillStyle = '#a05a34';
    g.fillRect(hx, bottom - 67, 60, 2);

    g.fillStyle = '#a8d8ff';                       // upstairs windows
    g.fillRect(hx + 8, bottom - 60, 12, 15);
    g.fillRect(hx + 38, bottom - 60, 12, 15);
    g.fillStyle = '#6c381c';
    g.fillRect(hx + 13, bottom - 60, 2, 15);
    g.fillRect(hx + 43, bottom - 60, 2, 15);
    g.fillRect(hx + 8, bottom - 53, 12, 2);
    g.fillRect(hx + 38, bottom - 53, 12, 2);

    g.fillStyle = '#3a2a44';                       // the door, up the stoop
    g.fillRect(hx + 36, bottom - 38, 15, 22);
    g.fillStyle = '#ffe9a8';
    g.fillRect(hx + 38, bottom - 36, 11, 4);       // fanlight, someone's home
    g.fillStyle = '#f7d51d';
    g.fillRect(hx + 38, bottom - 24, 2, 3);        // doorknob

    for (s = 0; s < 4; s++) {                      // the stoop itself
      g.fillStyle = s % 2 ? '#d2c2a6' : '#c2b296';
      g.fillRect(hx + 6 + s * 8, bottom - 4 - s * 4, 46 - s * 8, 4);
    }
    g.fillStyle = '#4a4a56';                       // the iron railing, stepped
    for (s = 0; s < 4; s++) {
      g.fillRect(hx + 6 + s * 8, bottom - 12 - s * 4, 2, 9);
      g.fillRect(hx + 6 + s * 8, bottom - 13 - s * 4, 10, 2);
    }
  };

  var TOKEN_SPIN = null;

  Game.prototype.renderEnts = function (g) {
    var cam = Math.floor(this.cam.x), i, e, x, y, f;
    if (!TOKEN_SPIN) TOKEN_SPIN = [SPR.token.a, SPR.token.a, SPR.token.a,
                                   SPR.token.b, SPR.token.c, SPR.token.b];
    for (i = 0; i < this.ents.length; i++) {
      e = this.ents[i];
      x = Math.round(e.x - cam);
      y = Math.round(e.y);
      if (x < -64 || x > VW + 64) continue;

      switch (e.kind) {
        case 'token':
          // weighted so a token spends most of its spin face-on, not edge-on
          f = TOKEN_SPIN[Math.floor(this.frame / 6) % TOKEN_SPIN.length];
          g.drawImage(f, x, y);
          break;
        case 'pigeon':
          if (e.dead && !e.flip) g.drawImage(SPR.pigeon.flat, x - 1, y);
          else this.blit(g, e.flip ? SPR.pigeon.flat : (Math.floor(e.t / 12) % 2 ? SPR.pigeon.w1 : SPR.pigeon.w2),
                         x - 1, y - 2, e.dir, e.flip);
          break;
        case 'rat':
          if (e.state === 'lid') {
            f = e.vx !== 0 ? (Math.floor(e.t / 4) % 2 ? SPR.rat.lid : SPR.rat.lid2) : SPR.rat.lid;
            this.blit(g, f, x - 2, y - 5, 1, e.flip);
          } else {
            this.blit(g, Math.floor(e.t / 9) % 2 ? SPR.rat.w1 : SPR.rat.w2, x - 1, y - 4, e.dir, e.flip);
          }
          break;
        case 'gull':
          this.blit(g, Math.floor(e.t / 10) % 2 ? SPR.gull.w1 : SPR.gull.w2, x - 1, y - 4, e.dir, e.flip);
          break;
        case 'notice':
          g.drawImage(SPR.notice, x, y);
          break;
        case 'boss':
          f = Math.floor(e.t / 12) % 2 ? SPR.boss.w1 : SPR.boss.w2;
          if (e.hurt > 0 && Math.floor(e.t / 3) % 2) break;
          this.blit(g, f, x - 2, y - 2, e.dir, e.flip);
          break;
        case 'pizza': g.drawImage(SPR.pizza, x, y); break;
        case 'eggcream': g.drawImage(SPR.eggcream, x, y); break;
        case 'metrocard':
          g.drawImage(Math.floor(this.frame / 6) % 2 ? SPR.metrocard.a : SPR.metrocard.b, x, y);
          break;
        case 'sandwich': g.drawImage(SPR.sandwich, x, y); break;
      }
    }
    // bottle caps
    for (i = 0; i < this.caps.length; i++) {
      var c = this.caps[i];
      g.drawImage(Math.floor(c.t / 4) % 2 ? SPR.cap.a : SPR.cap.b,
                  Math.round(c.x - cam), Math.round(c.y));
    }
  };

  /* Draw a sprite, optionally mirrored or upside down. */
  Game.prototype.blit = function (g, img, x, y, dir, flipY) {
    if (!img) return;
    if (dir >= 0 && !flipY) { g.drawImage(img, x, y); return; }
    g.save();
    g.translate(x + (dir < 0 ? img.width : 0), y + (flipY ? img.height : 0));
    g.scale(dir < 0 ? -1 : 1, flipY ? -1 : 1);
    g.drawImage(img, 0, 0);
    g.restore();
  };

  Game.prototype.renderPlayer = function (g) {
    var p = this.player;
    if (!p) return;
    if (this.mode === 'warp' && this.warpT > 26) return;   // gone down the stairs
    if (p.inv > 0 && Math.floor(this.frame / 3) % 2) return;

    var palIdx = 0;
    if (p.star > 0) palIdx = 2 + Math.floor(this.frame / 4) % 3;
    else if (p.fire) palIdx = 1;

    var big = p.big;
    if (p.grow > 0) big = (Math.floor(p.grow / 4) % 2) ? !p.big : p.big;
    var sheet = big ? SPR.salBig[palIdx] : SPR.salSmall[palIdx];

    var pose = 'idle';
    if (this.mode === 'dying') pose = 'dead';
    else if (p.ducking && big) pose = 'duck';
    else if (!p.onGround) pose = 'jump';
    else if ((keys.left && p.vx > 0.4) || (keys.right && p.vx < -0.4)) pose = 'skid';
    else if (Math.abs(p.vx) > 0.06) {
      pose = ['walk1', 'walk2', 'walk3', 'walk2'][Math.floor(p.anim) % 4];
    }
    if (this.mode === 'clear' && this.clearT < 60) pose = big ? 'duck' : 'idle';

    var img = sheet[pose] || sheet.idle;
    var x = Math.round(p.x - this.cam.x) - 1;
    var y = Math.round(p.y) - 2;
    if (big && p.ducking) y = Math.round(p.y) - 12;
    this.blit(g, img, x, y, p.dir, false);
  };

  Game.prototype.renderParts = function (g) {
    var cam = Math.floor(this.cam.x), i;
    for (i = 0; i < this.parts.length; i++) {
      var p = this.parts[i];
      var x = Math.round(p.x - cam), y = Math.round(p.y);
      if (p.token) {
        var f = [SPR.token.a, SPR.token.b, SPR.token.c, SPR.token.b][Math.floor(p.t / 3) % 4];
        g.drawImage(f, x, y);
      } else {
        g.fillStyle = THEME[this.theme].brick;
        g.fillRect(x, y, 5, 5);
        g.fillStyle = THEME[this.theme].brickDark;
        g.fillRect(x, y + 3, 5, 2);
      }
    }
    for (i = 0; i < this.pops.length; i++) {
      var q = this.pops[i];
      text(g, q.text, Math.round(q.x - cam), Math.round(q.y), '#ffffff', 1);
    }
  };

  /* HUD text gets a hard shadow so it stays readable over clouds and sky. */
  function hud(g, str, x, y, color, scale) {
    text(g, str, x + 1, y + 1, 'rgba(0,0,0,0.65)', scale);
    text(g, str, x, y, color, scale);
  }

  Game.prototype.renderHUD = function (g) {
    var y = 6;
    hud(g, 'SAL', 12, y, '#ffffff');
    hud(g, pad(this.score, 6), 12, y + 9, '#ffffff');

    g.drawImage(SPR.token.b, 74, y - 4, 10, 10);
    hud(g, 'X' + pad(this.tokens, 2), 86, y + 1, '#ffffff');

    hud(g, 'WORLD', 146, y, '#ffffff');
    hud(g, this.level.world, 152, y + 9, '#ffffff');

    hud(g, 'TIME', 208, y, '#ffffff');
    hud(g, pad(this.time, 3), 214, y + 9, this.time <= 100 ? '#ff6a4a' : '#ffffff');

    // lives, bottom-left, out of the way
    g.drawImage(SPR.salSmall[0].idle, 8, VH - 20, 9, 12);
    hud(g, 'X' + this.lives, 20, VH - 17, '#ffffff');

    if (this.msg) {
      var a = Math.min(1, this.msg.t / 30);
      g.globalAlpha = a;
      g.fillStyle = 'rgba(0,0,0,0.6)';
      g.fillRect(0, 100, VW, 22);
      textCentered(g, this.msg.text, 107, '#f7d51d', 1);
      g.globalAlpha = 1;
    }

    if (this.boss && !this.boss.dead) {
      hud(g, 'RENT', 96, VH - 17, '#ff6a4a');
      for (var i = 0; i < this.boss.hp; i++) {
        g.fillStyle = '#ff3a2a';
        g.fillRect(126 + i * 8, VH - 17, 6, 7);
      }
    }
  };

  Game.prototype.renderGameOver = function (g) {
    g.fillStyle = 'rgba(0,0,0,0.75)';
    g.fillRect(0, 0, VW, VH);
    if (this.won) {
      textCentered(g, 'YOU SAVED THE BLOCK', 84, '#f7d51d', 1);
      textCentered(g, 'THANK YOU SAL', 100, '#ffffff', 1);
      textCentered(g, 'SCORE ' + pad(this.score, 6), 124, '#ffffff', 1);
    } else {
      textCentered(g, 'GAME OVER', 96, '#ffffff', 2);
      textCentered(g, 'SCORE ' + pad(this.score, 6), 130, '#ffffff', 1);
    }
    textCentered(g, 'TOP ' + pad(this.high, 6), 146, '#9ad0ff', 1);
  };

  Game.prototype.renderTitle = function (g) {
    var t = this.titleT;
    var grd = g.createLinearGradient(0, 0, 0, VH);
    grd.addColorStop(0, '#101538');
    grd.addColorStop(0.55, '#3b3f7a');
    grd.addColorStop(1, '#c86a3a');
    g.fillStyle = grd;
    g.fillRect(0, 0, VW, VH);

    this.renderSkyline(g, t * 0.12, 0.55, '#232a52', 96);
    this.renderSkyline(g, t * 0.22, 1.0, '#151a38', 118);
    this.bankTower(g, 206, 44);

    // a couple of pigeons crossing the title card
    var px = (t * 0.7) % 320 - 40;
    this.blit(g, Math.floor(t / 10) % 2 ? SPR.gull.w1 : SPR.gull.w2, px, 40 + Math.sin(t * 0.04) * 6, 1, false);

    g.fillStyle = 'rgba(0,0,0,0.45)';
    g.fillRect(0, 54, VW, 74);

    textCentered(g, 'SUPER', 62, '#f7d51d', 3);
    textCentered(g, 'HOOD BROS', 88, '#ffffff', 3);
    textCentered(g, 'WASHINGTON PARK', 116, '#ff8a4a', 1);

    g.drawImage(SPR.salSmall[0].idle, 40, 150, 24, 32);
    this.blit(g, SPR.pigeon.w1, 196, 154, -1, false);
    g.fillStyle = '#2a2a34';
    g.fillRect(0, 182, VW, 6);
    g.fillStyle = '#3a3a48';
    g.fillRect(0, 188, VW, VH - 188);

    if (Math.floor(t / 26) % 2) textCentered(g, 'PRESS ENTER', 198, '#ffffff', 1);
    textCentered(g, 'TOP ' + pad(this.high, 6), 212, '#9ad0ff', 1);
    textCentered(g, 'ARROWS MOVE   Z JUMP   X RUN/THROW', 226, '#c8c8d8', 1);
  };

  function pad(n, w) {
    var s = String(Math.max(0, Math.floor(n)));
    while (s.length < w) s = '0' + s;
    return s;
  }

  /* ── boot ──────────────────────────────────────────────────────────── */

  var game = null;

  function fit() {
    var cv = game.cv;
    var wrap = cv.parentElement;
    var s = Math.max(1, Math.min(
      Math.floor(wrap.clientWidth / VW),
      Math.floor(wrap.clientHeight / VH)));
    if (wrap.clientWidth / VW < 1 || wrap.clientHeight / VH < 1) {
      s = Math.min(wrap.clientWidth / VW, wrap.clientHeight / VH);
    }
    cv.style.width = Math.floor(VW * s) + 'px';
    cv.style.height = Math.floor(VH * s) + 'px';
  }

  function loop(ts) {
    requestAnimationFrame(loop);
    if (!game.last) game.last = ts;
    var dt = Math.min(0.25, (ts - game.last) / 1000);
    game.last = ts;
    game.acc += dt;
    var guard = 0;
    while (game.acc >= STEP && guard++ < 8) {
      game.acc -= STEP;
      game.update();
    }
    game.render();
  }

  function boot() {
    var cv = document.getElementById('screen');
    cv.width = VW; cv.height = VH;
    game = new Game(cv);
    fit();
    global.addEventListener('resize', fit);

    global.addEventListener('keydown', function (ev) {
      var k = KEYMAP[ev.code];
      if (!k) return;
      ev.preventDefault();
      if (k === 'run' && game.player && game.player.fire && !keys.run) game.throwCap();
      if (k === 'mute') { SND.toggle(); return; }
      press(k);
      SND.resume();
    });
    global.addEventListener('keyup', function (ev) {
      var k = KEYMAP[ev.code];
      if (!k) return;
      ev.preventDefault();
      release(k);
    });
    global.addEventListener('blur', clearKeys);

    /* On-screen controls: anything carrying data-key is a button — the phone
     * pad and the key hints in the footer both. Pointer events, delegated from
     * the document, because that is what makes them feel like buttons:
     *   - the press lands on contact, not on click, and never waits 300ms;
     *   - a finger sliding from one button to the next hands the key over,
     *     which is how you get run+jump without lifting off;
     *   - two fingers hold two keys;
     *   - a pointer lost to a phone call or a swipe releases instead of
     *     sticking down.
     * Touch gives the first target implicit capture, so pointermove keeps
     * coming to it — hence the hit test rather than per-element listeners. */
    var held = {};                             // pointerId → key it is holding

    function keyAt(x, y) {
      var el = document.elementFromPoint(x, y);
      while (el && !(el.dataset && el.dataset.key)) el = el.parentElement;
      return el ? el.dataset.key : null;
    }
    function holders(k) {
      var n = 0;
      for (var id in held) if (held[id] === k) n++;
      return n;
    }
    function paint(k, on) {
      var els = document.querySelectorAll('[data-key="' + k + '"]');
      for (var i = 0; i < els.length; i++) els[i].classList.toggle('on', on);
    }
    function hold(id, k) {
      var was = held[id] || null;
      if (was === k) return;
      if (was) {
        delete held[id];
        if (!holders(was)) { release(was); paint(was, false); }
      }
      if (!k) return;
      held[id] = k;
      if (holders(k) > 1) return;              // another finger already has it
      if (k === 'mute') { SND.toggle(); paint(k, true); return; }
      if (k === 'run' && game.player && game.player.fire && !keys.run)
        game.throwCap();
      press(k); paint(k, true); SND.resume();
    }

    document.addEventListener('pointerdown', function (ev) {
      var k = keyAt(ev.clientX, ev.clientY);
      if (!k) return;
      ev.preventDefault();                     // no focus ring, no text select
      hold(ev.pointerId, k);
    });
    document.addEventListener('pointermove', function (ev) {
      if (!(ev.pointerId in held)) return;     // only a pointer already down
      ev.preventDefault();
      hold(ev.pointerId, keyAt(ev.clientX, ev.clientY));
    });
    // up and cancel can land anywhere, including off the window
    ['pointerup', 'pointercancel'].forEach(function (name) {
      global.addEventListener(name, function (ev) { hold(ev.pointerId, null); });
    });

    requestAnimationFrame(loop);

    /* test + debug hooks */
    global.__sm = {
      game: game,
      press: press,
      release: release,
      clear: clearKeys,          // drop every key and every buffered press
      tap: function (k, frames) {
        press(k);
        setTimeout(function () { release(k); }, frames || 80);
      },
      start: function () { press('start'); },
      state: function () {
        var p = game.player;
        return {
          mode: game.mode, level: game.levelId, frame: game.frame,
          score: game.score, tokens: game.tokens, lives: game.lives,
          time: game.time,
          player: p ? { x: p.x, y: p.y, vx: p.vx, vy: p.vy, big: p.big,
                        fire: p.fire, star: p.star, onGround: p.onGround } : null,
          ents: game.ents ? game.ents.length : 0,
          boss: game.boss ? game.boss.hp : null
        };
      },
      goto: function (id) { game.loadLevel(id); },
      levels: function () { return LEVELS.ids(); }
    };
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(window);
