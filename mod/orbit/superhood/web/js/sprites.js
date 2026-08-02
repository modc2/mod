/* sprites.js — every sprite in SUPER HOOD BROS. is drawn from a pixel grid.
 *
 * A grid is an array of equal-ish length strings; each character maps to a
 * colour in a palette ('.' is transparent). Rows shorter than the widest row
 * are padded with transparency, so the art below can be written by eye.
 *
 * Everything is pre-rendered once into offscreen canvases at load time — the
 * game loop only ever does drawImage.
 */
(function (global) {
  'use strict';

  var BASE = {
    K: '#000000',   // outline
    W: '#ffffff',   // white / eye
    E: '#c8c8c8',   // light grey
    e: '#7a7a7a',   // grey
    d: '#3c3c3c',   // dark grey
    S: '#f2b48c',   // skin
    s: '#c98a63',   // skin shadow
    R: '#d02b1f',   // hoodie
    r: '#8f1a12',   // hoodie shadow
    B: '#2b4fd0',   // jeans / cap
    b: '#16307f',   // jeans shadow
    C: '#2b4fd0',   // cap
    c: '#16307f',   // cap shadow
    N: '#8a5a2b',   // shoe / wood / brown
    n: '#513315',   // dark brown
    Y: '#f7d51d',   // yellow
    y: '#c39b00',   // dark yellow
    O: '#e8801f',   // orange
    o: '#a8530a',   // dark orange
    G: '#3aa03a',   // green
    g: '#1f6b2f',   // dark green
    P: '#f2a0b4',   // pink
    p: '#c2607a',   // dark pink
    A: '#7fd6ff',   // sky blue / glass
    a: '#3a86c8',   // deep blue
    M: '#b06cf0',   // magenta
    T: '#f5e6c8'    // cream / cheese
  };

  function pal(over) {
    var p = {}, k;
    for (k in BASE) p[k] = BASE[k];
    for (k in (over || {})) p[k] = over[k];
    return p;
  }

  /* Render one grid into a canvas. */
  function px(grid, palette) {
    var w = 0, i;
    for (i = 0; i < grid.length; i++) w = Math.max(w, grid[i].length);
    var h = grid.length;
    var cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    var g = cv.getContext('2d');
    for (var y = 0; y < h; y++) {
      var row = grid[y];
      for (var x = 0; x < row.length; x++) {
        var ch = row[x];
        if (ch === '.' || ch === ' ') continue;
        var col = palette[ch];
        if (!col) continue;
        g.fillStyle = col;
        g.fillRect(x, y, 1, 1);
      }
    }
    return cv;
  }

  /* ── SAL, small (12 x 16) ───────────────────────────────────────────── */

  var SAL_S = {
    idle: [
      '...ccccc....',
      '..cCCCCCcc..',
      '..cCCCCCccc.',
      '..SSSSSSSs..',
      '..SSWKSSSs..',
      '..SSSSSSSs..',
      '...sSSSss...',
      '..RRRRRRR...',
      '.SRRRRRRRS..',
      '.SRRRRRRRS..',
      '.sRRRRRRRs..',
      '..rRRRRRr...',
      '..BBB.BBB...',
      '..BBB.BBB...',
      '..bbb.bbb...',
      '.NNNN.NNNN..'
    ],
    walk1: [
      '...ccccc....',
      '..cCCCCCcc..',
      '..cCCCCCccc.',
      '..SSSSSSSs..',
      '..SSWKSSSs..',
      '..SSSSSSSs..',
      '...sSSSss...',
      '..RRRRRRRS..',
      '.SRRRRRRRS..',
      '.SRRRRRRRr..',
      '..RRRRRRr...',
      '..RRRRRr....',
      '..BBBBB.....',
      '..BBB.bb....',
      '.bbb...bb...',
      'NNNN...NNN..'
    ],
    walk2: [
      '...ccccc....',
      '..cCCCCCcc..',
      '..cCCCCCccc.',
      '..SSSSSSSs..',
      '..SSWKSSSs..',
      '..SSSSSSSs..',
      '...sSSSss...',
      '..RRRRRRR...',
      '..RRRRRRRS..',
      '.SRRRRRRRS..',
      '.SrRRRRRr...',
      '...RRRRR....',
      '...BBBBB....',
      '...BBBBB....',
      '...bb.bb....',
      '..NNN.NNN...'
    ],
    walk3: [
      '...ccccc....',
      '..cCCCCCcc..',
      '..cCCCCCccc.',
      '..SSSSSSSs..',
      '..SSWKSSSs..',
      '..SSSSSSSs..',
      '...sSSSss...',
      '.SRRRRRRR...',
      '.SRRRRRRRS..',
      '..rRRRRRRS..',
      '...RRRRRRs..',
      '....RRRRRr..',
      '.....BBBBB..',
      '....bb.BBB..',
      '...bb...bbb.',
      '..NNN...NNNN'
    ],
    jump: [
      '...ccccc....',
      '..cCCCCCcc..',
      '..cCCCCCccc.',
      '..SSSSSSSs..',
      '..SSWKSSSs..',
      '..SSSSSSSs..',
      'S..sSSSss...',
      'SSRRRRRRRS..',
      '.SRRRRRRRSS.',
      '..RRRRRRRs..',
      '..RRRRRRr...',
      '..RRRRRr....',
      '..BBBBB.....',
      '..BB.bbb....',
      '.NNN...bb...',
      '.NNN...NNN..'
    ],
    skid: [
      '....ccccc...',
      '..ccCCCCCc..',
      '.cccCCCCCc..',
      '..sSSSSSSS..',
      '..sSSSKWSS..',
      '..sSSSSSSS..',
      '...ssSSSs...',
      '..SRRRRRR...',
      '.SSRRRRRRr..',
      '..SRRRRRRr..',
      '...RRRRRRr..',
      '...RRRRRr...',
      '...BBBBB....',
      '..bBBBBB....',
      '..bb...bb...',
      '.NNNN.NNNN..'
    ],
    dead: [
      '...ccccc....',
      '..cCCCCCcc..',
      '..cCCCCCccc.',
      '..SSSSSSSs..',
      '..SKSSSKSs..',
      '..SSSSSSSs..',
      '...sKKKss...',
      '.SRRRRRRRS..',
      'S.RRRRRRR.S.',
      '..RRRRRRR...',
      '..rRRRRRr...',
      '...RRRRR....',
      '..BBB.BBB...',
      '.bbb...bbb..',
      'NNN.....NNN.',
      '............'
    ]
  };

  /* ── SAL, big (14 x 26) ─────────────────────────────────────────────── */

  var SAL_B = {
    idle: [
      '....ccccc.....',
      '...cCCCCCcc...',
      '...cCCCCCcccc.',
      '...SSSSSSSSs..',
      '...SSWKSSSSs..',
      '...SSSSSSSSs..',
      '...SSSSSSSs...',
      '....ssSSSs....',
      '.....sSSs.....',
      '...RRRRRRR....',
      '..RRRRRRRRR...',
      '.SRRRRRRRRRS..',
      '.SRRRRRRRRRS..',
      '.SRRRRRRRRRS..',
      '.sRRRRRRRRRs..',
      '..rRRRRRRRr...',
      '...RRRRRRR....',
      '...BBBBBBB....',
      '...BBB.BBB....',
      '...BBB.BBB....',
      '...BBB.BBB....',
      '...BBB.BBB....',
      '...bbb.bbb....',
      '...bbb.bbb....',
      '..NNNN.NNNN...',
      '..NNNN.NNNN...'
    ],
    walk1: [
      '....ccccc.....',
      '...cCCCCCcc...',
      '...cCCCCCcccc.',
      '...SSSSSSSSs..',
      '...SSWKSSSSs..',
      '...SSSSSSSSs..',
      '...SSSSSSSs...',
      '....ssSSSs....',
      '.....sSSs.....',
      '...RRRRRRRS...',
      '..RRRRRRRRSS..',
      '.SRRRRRRRRRS..',
      '.SRRRRRRRRRs..',
      '.SRRRRRRRRr...',
      '..RRRRRRRr....',
      '..RRRRRRr.....',
      '..RRRRRr......',
      '..BBBBBB......',
      '..BBBBB.bb....',
      '..BBBB..bb....',
      '.bBBB....bb...',
      '.bbb.....bbb..',
      'NNNN.....NNN..',
      'NNNN......NNN.',
      '..............',
      '..............'
    ],
    walk2: [
      '....ccccc.....',
      '...cCCCCCcc...',
      '...cCCCCCcccc.',
      '...SSSSSSSSs..',
      '...SSWKSSSSs..',
      '...SSSSSSSSs..',
      '...SSSSSSSs...',
      '....ssSSSs....',
      '.....sSSs.....',
      '...RRRRRRR....',
      '...RRRRRRRS...',
      '..RRRRRRRRS...',
      '.SRRRRRRRRR...',
      '.SRRRRRRRRr...',
      '..rRRRRRRr....',
      '....RRRRR.....',
      '....RRRRR.....',
      '....BBBBB.....',
      '....BBBBB.....',
      '...BBBBBBB....',
      '...BBB.BBB....',
      '...bbb.bbb....',
      '...bbb.bbb....',
      '..NNN...NNN...',
      '..NNN...NNN...',
      '..............'
    ],
    walk3: [
      '....ccccc.....',
      '...cCCCCCcc...',
      '...cCCCCCcccc.',
      '...SSSSSSSSs..',
      '...SSWKSSSSs..',
      '...SSSSSSSSs..',
      '...SSSSSSSs...',
      '....ssSSSs....',
      '.....sSSs.....',
      '..SRRRRRRR....',
      '.SSRRRRRRRR...',
      '.SRRRRRRRRRS..',
      '..rRRRRRRRRS..',
      '...RRRRRRRRs..',
      '....RRRRRRr...',
      '.....RRRRRr...',
      '......RRRRr...',
      '......BBBBB...',
      '....bbBBBBB...',
      '....bb.BBBB...',
      '...bb...BBBb..',
      '..bbb....bbb..',
      '..NNN.....NNNN',
      '.NNNN.....NNNN',
      '..............',
      '..............'
    ],
    jump: [
      '....ccccc.....',
      '...cCCCCCcc...',
      '...cCCCCCcccc.',
      '...SSSSSSSSs..',
      '...SSWKSSSSs..',
      '...SSSSSSSSs..',
      '...SSSSSSSs...',
      '....ssSSSs....',
      'S....sSSs.....',
      'SS.RRRRRRR....',
      '.SSRRRRRRRRS..',
      '..SRRRRRRRRSS.',
      '..RRRRRRRRRs..',
      '..RRRRRRRRr...',
      '..RRRRRRRr....',
      '..RRRRRRr.....',
      '..BBBBBB......',
      '..BBBBBB......',
      '..BBB.bbb.....',
      '..BBB...bb....',
      '.NNNN....bb...',
      '.NNNN....bbb..',
      '.........NNNN.',
      '.........NNNN.',
      '..............',
      '..............'
    ],
    skid: [
      '.....ccccc....',
      '...ccCCCCCc...',
      '.ccccCCCCCc...',
      '..sSSSSSSSS...',
      '..sSSSSKWSS...',
      '..sSSSSSSSS...',
      '...sSSSSSSS...',
      '....sSSSss....',
      '.....sSSs.....',
      '...SRRRRRRR...',
      '..SSRRRRRRRR..',
      '..SRRRRRRRRr..',
      '...RRRRRRRRr..',
      '...RRRRRRRRr..',
      '....RRRRRRr...',
      '....RRRRRr....',
      '....BBBBB.....',
      '...BBBBBB.....',
      '..bBBBBBB.....',
      '..bbBBB.bb....',
      '..bb....bb....',
      '..bb....bbb...',
      '.NNNN..NNNN...',
      '.NNNN..NNNN...',
      '..............',
      '..............'
    ],
    duck: [
      '..............',
      '..............',
      '..............',
      '..............',
      '..............',
      '..............',
      '..............',
      '....ccccc.....',
      '...cCCCCCcc...',
      '...cCCCCCcccc.',
      '...SSSSSSSSs..',
      '...SSWKSSSSs..',
      '...SSSSSSSSs..',
      '....ssSSSs....',
      '..RRRRRRRRS...',
      '.SRRRRRRRRRS..',
      '.SRRRRRRRRRs..',
      '..rRRRRRRRr...',
      '..BBBBBBBBB...',
      '..BBBBBBBBB...',
      '..BBB...BBB...',
      '..bbb...bbb...',
      '.NNNN...NNNN..',
      '.NNNN...NNNN..',
      '..............',
      '..............'
    ],
    dead: SAL_S.dead
  };

  /* ── enemies ───────────────────────────────────────────────────────── */

  var PIGEON = {
    w1: [
      '................',
      '......ddd.......',
      '.....dEEEd......',
      '.....dEKEd..oo..',
      '.....dEEEddo....',
      '......dEEEEd....',
      '....ddeeeEEd....',
      '...deeeeeeEd....',
      '..deeeeeeeed....',
      '..deeMMeeeed....',
      '..dGeeeeeeed....',
      '...ddeeeeed.....',
      '.....ddddd......',
      '.....O...O......',
      '....OOO.OOO.....',
      '................'
    ],
    w2: [
      '................',
      '......ddd.......',
      '.....dEEEd......',
      '.....dEKEd..oo..',
      '.....dEEEddo....',
      '......dEEEEd....',
      '....ddeeeEEd....',
      '..ddeeeeeeEd....',
      '.deeeeeeeeed....',
      '.deeeMMeeeed....',
      '..dGeeeeeeed....',
      '...ddeeeeed.....',
      '.....ddddd......',
      '......OOO.......',
      '.....OOOOO......',
      '................'
    ],
    flat: [
      '................',
      '................',
      '................',
      '................',
      '................',
      '................',
      '................',
      '................',
      '................',
      '................',
      '..dd........dd..',
      '.deeddddddddeed.',
      '.deeeeMMeeeeeed.',
      '.dEeeeeeeeeeEed.',
      '..dddddddddddd..',
      '................'
    ]
  };

  var RAT = {
    w1: [
      '................',
      '................',
      '................',
      '.........dd.....',
      '.....ddddeed....',
      '...ddeeeeeeed...',
      '..deeeeeeeeKd...',
      '.deeeeeeeeeed...',
      'deeeeeeeeeeed...',
      'ndeeeeeeeeedd...',
      '.nddeeeeedd.....',
      '..nnddddd.......',
      '....P...P.......',
      '...PPP.PPP......',
      '................',
      '................'
    ],
    w2: [
      '................',
      '................',
      '................',
      '.........dd.....',
      '.....ddddeed....',
      '...ddeeeeeeed...',
      '..deeeeeeeeKd...',
      '.deeeeeeeeeed...',
      'deeeeeeeeeeed...',
      '.deeeeeeeeedd...',
      'n.ddeeeeeddP....',
      '.nnnddddd.......',
      '.....PPPP.......',
      '....PPPPPP......',
      '................',
      '................'
    ],
    lid: [
      '................',
      '................',
      '................',
      '................',
      '................',
      '.....ddddd......',
      '...ddEEEEEdd....',
      '..dEEEEEEEEEd...',
      '..dEEdEEEdEEd...',
      '..dEEEEEEEEEd...',
      '..deEEEEEEEed...',
      '..ddeeeeeeedd...',
      '...ddddddddd....',
      '................',
      '................',
      '................'
    ],
    lid2: [
      '................',
      '................',
      '................',
      '................',
      '................',
      '.....ddddd......',
      '...ddeeeeedd....',
      '..dEEEEEEEEEd...',
      '..dEEEdEdEEEd...',
      '..dEEEEEEEEEd...',
      '..deeEEEEEeed...',
      '..ddeeeeeeedd...',
      '...ddddddddd....',
      '................',
      '................',
      '................'
    ]
  };

  var GULL = {
    w1: [
      '................',
      '................',
      '..EE......EE....',
      '.EEEE....EEEE...',
      'EEEEEE..EEEEEE..',
      '.EEEEEEEEEEEE...',
      '...EEEEEEEE.....',
      '....EEEEEE......',
      '....EWKWEE.OO...',
      '....EEEEEEOO....',
      '.....EEEEE......',
      '......EEE.......',
      '.......O........',
      '................',
      '................',
      '................'
    ],
    w2: [
      '................',
      '................',
      '................',
      '................',
      '................',
      '...EEE....EEE...',
      '....EEEEEEEE....',
      '....EEEEEE......',
      '....EWKWEE.OO...',
      '....EEEEEEOO....',
      '.....EEEEE......',
      '...EEEEEEEEE....',
      '..EEEE.O..EEEE..',
      '.EEE........EEE.',
      '................',
      '................'
    ]
  };

  /* THE LANDLORD — 1-3 boss. 32 x 32. */
  var BOSS = {
    w1: [
      '..........KKKKKKKKKKKK..........',
      '.........KddddddddddddK.........',
      '........KdddddddddddddK.........',
      '.......KKKKKKKKKKKKKKKKKK.......',
      '.......KEEEEEEEEEEEEEEEEK.......',
      '........SSSSSSSSSSSSSSS.........',
      '........SSWKSSSSSSWKSSS.........',
      '........SSSSSSSSSSSSSSS.........',
      '........SSSSSKKKKSSSSSS.........',
      '.........sSSSSSSSSSSSs..........',
      '..........sSSSSSSSSs............',
      '.......dddddddddddddddd.........',
      '.....ddddWWWWWWWWWWdddddd.......',
      '....ddddWWWWWWWWWWWWdddddd......',
      '...ddddWWWWddddddWWWWddddd......',
      '..dddddWWWWdKKKKdWWWWdddddd.....',
      '.SSdddWWWWWdKKKKdWWWWWdddSS.....',
      '.SSdddWWWWWWddddWWWWWWdddSS.....',
      '.sSddddWWWWWWWWWWWWWWddddSs.....',
      '..sdddddWWWWWWWWWWWWddddds......',
      '...dddddddWWWWWWWWdddddddd......',
      '....dddddddddddddddddddd........',
      '.....dddddddddddddddddd.........',
      '.....dddddd......dddddd.........',
      '.....dddddd......dddddd.........',
      '.....dddddd......dddddd.........',
      '.....dddddd......dddddd.........',
      '.....KKKKKK......KKKKKK.........',
      '....KKKKKKKK....KKKKKKKK........',
      '....KKKKKKKK....KKKKKKKK........',
      '................................',
      '................................'
    ],
    w2: [
      '..........KKKKKKKKKKKK..........',
      '.........KddddddddddddK.........',
      '........KdddddddddddddK.........',
      '.......KKKKKKKKKKKKKKKKKK.......',
      '.......KEEEEEEEEEEEEEEEEK.......',
      '........SSSSSSSSSSSSSSS.........',
      '........SSWKSSSSSSWKSSS.........',
      '........SSSSSSSSSSSSSSS.........',
      '........SSSSKKKKKKSSSSS.........',
      '.........sSSKKKKKKSSSSs.........',
      '..........sSSSSSSSSs............',
      '.......dddddddddddddddd.........',
      '.....ddddWWWWWWWWWWdddddd.......',
      '....ddddWWWWWWWWWWWWdddddd......',
      '...ddddWWWWddddddWWWWddddd......',
      '..dddddWWWWdKKKKdWWWWdddddd.....',
      'SSdddddWWWWdKKKKdWWWWdddddSS....',
      'SSdddddWWWWWddddWWWWWdddddSS....',
      'sSdddddWWWWWWWWWWWWWWddddd.s....',
      '..sddddWWWWWWWWWWWWWWdddd.......',
      '...dddddddWWWWWWWWdddddddd......',
      '....dddddddddddddddddddd........',
      '.....dddddddddddddddddd.........',
      '.....dddddd......dddddd.........',
      '.....dddddd......dddddd.........',
      '.....dddddd......dddddd.........',
      '.....ddddddd....ddddddd.........',
      '....KKKKKKKKK..KKKKKKKKK........',
      '...KKKKKKKKKK..KKKKKKKKKK.......',
      '...KKKKKKKKKK..KKKKKKKKKK.......',
      '................................',
      '................................'
    ]
  };

  /* An eviction notice — the boss's projectile. */
  var NOTICE = [
    '..........',
    '.WWWWWWWW.',
    '.WKKKKKKW.',
    '.WWWWWWWW.',
    '.WKKKKWWW.',
    '.WWWWWWWW.',
    '.WKKKKKKW.',
    '.WWWWWWWW.',
    '.WRRRWWWW.',
    '..........'
  ];

  /* ── items ─────────────────────────────────────────────────────────── */

  var PIZZA = [
    '................',
    '......nnnn......',
    '.....nOOOOn.....',
    '....nOTTTTOn....',
    '....nTTRTTTn....',
    '...nTTTTTTTTn...',
    '...nTTRTTRTTn...',
    '..nTTTTTTTTTTn..',
    '..nTTTTTTTTTTn..',
    '.nTTRTTTTTRTTTn.',
    '.nTTTTTTTTTTTTn.',
    'nTTTTTRTTTTTTTTn',
    'nNNNNNNNNNNNNNNn',
    '.nNNNNNNNNNNNNn.',
    '..nnnnnnnnnnnn..',
    '................'
  ];

  var EGGCREAM = [
    '................',
    '.....WWWWW......',
    '....WWWWWWW.....',
    '....WEEEEEW.....',
    '....dWWWWWd.....',
    '....dnnnnnd.....',
    '....dnnnnnd.....',
    '....dnnnnnd.....',
    '....dnRRRnd.....',
    '....dnRRRnd.....',
    '....dnnnnnd.....',
    '....dnnnnnd.....',
    '....dEEEEEd.....',
    '....ddddddd.....',
    '................',
    '................'
  ];

  var METROCARD = {
    a: [
      '................',
      '................',
      '.KKKKKKKKKKKKKK.',
      '.KYYYYYYYYYYYYK.',
      '.KYOOOOOOOOOOYK.',
      '.KYOKKKKKKKKOYK.',
      '.KYOKYYYYYYKOYK.',
      '.KYOKYMMMMYKOYK.',
      '.KYOKYMMMMYKOYK.',
      '.KYOKYYYYYYKOYK.',
      '.KYOKKKKKKKKOYK.',
      '.KYOOOOOOOOOOYK.',
      '.KYYYYYYYYYYYYK.',
      '.KKKKKKKKKKKKKK.',
      '................',
      '................'
    ],
    b: [
      '................',
      '................',
      '.KKKKKKKKKKKKKK.',
      '.KMMMMMMMMMMMMK.',
      '.KMYYYYYYYYYYMK.',
      '.KMYKKKKKKKKYMK.',
      '.KMYKOOOOOOKYMK.',
      '.KMYKOWWWWOKYMK.',
      '.KMYKOWWWWOKYMK.',
      '.KMYKOOOOOOKYMK.',
      '.KMYKKKKKKKKYMK.',
      '.KMYYYYYYYYYYMK.',
      '.KMMMMMMMMMMMMK.',
      '.KKKKKKKKKKKKKK.',
      '................',
      '................'
    ]
  };

  /* A subway token. Four frames of spin. */
  var TOKEN = {
    a: [
      '................',
      '................',
      '.....yyyyy......',
      '....yYYYYYy.....',
      '...yYYyyyYYy....',
      '...yYyOOOyYy....',
      '...yYyOOOyYy....',
      '...yYyOOOyYy....',
      '...yYyOOOyYy....',
      '...yYyOOOyYy....',
      '...yYYyyyYYy....',
      '....yYYYYYy.....',
      '.....yyyyy......',
      '................',
      '................',
      '................'
    ],
    b: [
      '................',
      '................',
      '......yyy.......',
      '.....yYYYy......',
      '.....yYyYy......',
      '.....yYOYy......',
      '.....yYOYy......',
      '.....yYOYy......',
      '.....yYOYy......',
      '.....yYOYy......',
      '.....yYyYy......',
      '.....yYYYy......',
      '......yyy.......',
      '................',
      '................',
      '................'
    ],
    c: [
      '................',
      '................',
      '.......y........',
      '.......Yy.......',
      '.......Yy.......',
      '.......Yy.......',
      '.......Yy.......',
      '.......Yy.......',
      '.......Yy.......',
      '.......Yy.......',
      '.......Yy.......',
      '.......Yy.......',
      '.......y........',
      '................',
      '................',
      '................'
    ]
  };

  /* Egg sandwich — the 1-UP. */
  var SANDWICH = [
    '................',
    '................',
    '...nnnnnnnnnn...',
    '..nNNNNNNNNNNn..',
    '.nNNNNNNNNNNNNn.',
    '.nWWWWWWWWWWWWn.',
    '.nWWYYWWWWYYWWn.',
    '.nWWYYWWWWYYWWn.',
    '.nOOOOOOOOOOOOn.',
    '.nNNNNNNNNNNNNn.',
    '..nNNNNNNNNNNn..',
    '...nnnnnnnnnn...',
    '................',
    '................',
    '................',
    '................'
  ];

  /* A bottle cap — the projectile. */
  var CAP = {
    a: [
      '.KKKK.',
      'KRRRRK',
      'KRWWRK',
      'KRWWRK',
      'KRRRRK',
      '.KKKK.'
    ],
    b: [
      '.KKKK.',
      'KRWRRK',
      'KWRRWK',
      'KWRRWK',
      'KRRWRK',
      '.KKKK.'
    ]
  };

  /* ── street furniture ──────────────────────────────────────────────── */

  var HYDRANT = [
    '................',
    '.....ddddd......',
    '.....dRRRd......',
    '......RRR.......',
    '...ddRRRRRdd....',
    '...dRRRRRRRd....',
    '..ddRRRRRRRdd...',
    '..dRRRRRRRRRd...',
    '..dRRRRRRRRRd...',
    '..dRRRRRRRRRd...',
    '...dRRRRRRRd....',
    '...dRRRRRRRd....',
    '..ddRRRRRRRdd...',
    '..dddddddddddd..',
    '..dddddddddddd..',
    '................'
  ];

  var TRASH = [
    '................',
    '..dEEEEEEEEEd...',
    '..dEEEEEEEEEd...',
    '...ddddddddd....',
    '...eEEEEEEEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eEeEeEeEe....',
    '...eeeeeeeee....',
    '...ddddddddd....'
  ];

  /* A London plane tree — the ones that shade every Brooklyn street. */
  var TREE = [
    '.....gggggg.....',
    '...gggGGGGggg...',
    '..gGGGGGGGGGGg..',
    '.gGGGGGGGGGGGGg.',
    'gGGGGGGGGGGGGGGg',
    'gGGGGGGGGGGGGGGg',
    '.gGGGGGGGGGGGGg.',
    '..gGGGGGGGGGGg..',
    '...ggGGGGGGgg...',
    '.....gGGGGg.....',
    '......nNNn......',
    '......nNNn......',
    '......nNNn......',
    '......nNNn......',
    '.....nNNNNn.....',
    '....nnNNNNnn....'
  ];

  var BUSH = [
    '................',
    '................',
    '................',
    '................',
    '................',
    '.....gggg.......',
    '...ggGGGGgg.....',
    '..gGGGGGGGGg.g..',
    '.gGGGGGGGGGGgGg.',
    'gGGGGGGGGGGGGGGg',
    'gGGGGGGGGGGGGGGg',
    'gGGGGGGGGGGGGGGg',
    'gGGGGGGGGGGGGGGg',
    'gGGGGGGGGGGGGGGg',
    'gGGGGGGGGGGGGGGg',
    'gggggggggggggggg'
  ];

  /* ── palettes ──────────────────────────────────────────────────────── */

  var SAL_PALS = [
    pal({}),                                                  // 0 normal
    pal({ R: '#ffffff', r: '#c0c0d0', C: '#d02b1f', c: '#8f1a12',
          B: '#d02b1f', b: '#8f1a12' }),                      // 1 egg-cream
    pal({ R: '#f7d51d', r: '#c39b00', C: '#ffffff', c: '#c0c0d0',
          B: '#f7d51d', b: '#c39b00' }),                      // 2 star a
    pal({ R: '#3aa03a', r: '#1f6b2f', C: '#f7d51d', c: '#c39b00',
          B: '#3aa03a', b: '#1f6b2f' }),                      // 3 star b
    pal({ R: '#7fd6ff', r: '#3a86c8', C: '#f2a0b4', c: '#c2607a',
          B: '#7fd6ff', b: '#3a86c8' })                       // 4 star c
  ];

  function bake(set, palettes) {
    var out = [];
    for (var i = 0; i < palettes.length; i++) {
      var frames = {};
      for (var k in set) frames[k] = px(set[k], palettes[i]);
      out.push(frames);
    }
    return out;
  }

  function bakeOne(set, palette) {
    var frames = {};
    for (var k in set) frames[k] = px(set[k], palette || BASE);
    return frames;
  }

  var S = {
    /* salSmall[paletteIndex][pose] */
    salSmall: bake(SAL_S, SAL_PALS),
    salBig: bake(SAL_B, SAL_PALS),
    pigeon: bakeOne(PIGEON),
    rat: bakeOne(RAT),
    gull: bakeOne(GULL),
    boss: bakeOne(BOSS),
    notice: px(NOTICE, BASE),
    pizza: px(PIZZA, BASE),
    eggcream: px(EGGCREAM, BASE),
    metrocard: bakeOne(METROCARD),
    token: bakeOne(TOKEN),
    sandwich: px(SANDWICH, BASE),
    cap: bakeOne(CAP),
    hydrant: px(HYDRANT, BASE),
    trash: px(TRASH, BASE),
    tree: px(TREE, BASE),
    bush: px(BUSH, BASE),
    /* exposed so the renderer can tint things consistently */
    BASE: BASE,
    px: px,
    pal: pal
  };

  global.SPR = S;
})(window);
