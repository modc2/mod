// Washington Square Park, New York — the original freewash map.
//
// Laid out to the real park's plan: Fifth Avenue enters under the arch and runs
// dead straight to the fountain (the only straight walk in the park, and the
// widest), the cross walks kink around the plaza rather than through it, four
// diagonals bow off their chords, and everybody laps the perimeter loop.
// Everything else — 600-odd trees, every bench and lamp — is planted off that
// plan by the engine, which is why the place reads as designed.
const rP = 20 * 0.707 - 0.7;      // where a diagonal leaves the plaza kerb
const ARCH_Z = -70, EDGE = 88;

export default {
  id: 'washington',
  name: 'Washington Square Park',
  city: 'New York',
  tag: 'live from nyc',
  icon: '⛲',
  accent: '#a78bfa',
  blurb: 'Walk or skate the Fifth Avenue promenade, the fountain plaza and the tree-lined ' +
         'walks. Drum circles going, buskers pulling a crowd, chess hustlers in the south-west ' +
         'corner, half the park sat on the fountain rim, pigeons going up as you run through them.',
  size: 110, bound: 96,
  spawn: { x: 0, z: -36, face: 0 },
  ground: 'grass', outer: 'asphalt', wear: true,
  time: 'golden', weather: 'clear', music: 'drums',

  plaza: { x: 0, z: 0, r: 20, kerb: true },

  walks: [
    { p: [[0,-98],[0,ARCH_Z],[0,-46],[0,-19]], w: 15 },      // Fifth Avenue promenade
    { p: [[0,19],[0,34],[0,58],[0,98]], w: 10 },             // south walk to the Thompson gate
    { p: [[-98,0],[-58,0],[-32,-1],[-19,-1]], w: 10 },       // west arm of the cross walk
    { p: [[19,-1],[32,-1],[58,0],[98,0]], w: 10 },           // east arm
    { bow: [[ rP, rP], [ 98,  98],  10], w: 8 },             // four corner diagonals
    { bow: [[ rP,-rP], [ 98, -98], -12], w: 8 },
    { bow: [[-rP, rP], [-98,  98], -10], w: 8 },
    { bow: [[-rP,-rP], [-98, -98],  12], w: 8 },
    { loop: { x: 0, z: 0, ex: EDGE, ez: EDGE, r: 16 }, w: 7 },  // the lap everyone walks
    { bow: [[-rP, rP], [-58, 52], -6], w: 6 },               // connector to the chess plaza
    { bow: [[-EDGE+7, 10], [-60, 14], 3], w: 5 },            // …to the dog run
    { bow: [[ rP, rP], [ 56, 56],  7], w: 6 },               // …to the playground
  ],

  surfaces: [
    { k: 'pavement', x: -60, z: 48.6, w: 18, d: 14 },        // the chess apron is paved, not grass
  ],

  // Nothing self-plants in the arch sightline, the facilities, or the paved corners.
  keepOut: [
    { x: 0,   z: -75,  w: 40, d: 42 },      // the arch's sightline down Fifth Avenue
    { x: -62, z: 14,   w: 29, d: 22 },      // dog run
    { x: -60, z: 48.6, w: 22, d: 18 },      // chess apron
    { x: 56,  z: 56,   r: 19 },             // playground
    { x: 30,  z: 3,    r: 4.5 },            // Garibaldi
  ],

  // London planes shading every walk, looser groves on the lawns, and one
  // enormous English elm in the north-west corner that predates the park.
  planting: {
    allee:  { every: 11.5, back: 3.0, kinds: ['plane'], scale: [0.92, 1.20] },
    groves: { n: 130, kinds: { plane: 0.86, honey: 0.14 }, scale: [0.80, 1.25] },
    specials: [{ x: -74, z: -76, kind: 'elm', scale: 1.0 }],
  },
  furnish: { every: 12, lampEvery: 3, bench: true },

  props: [
    // lamps ringing the plaza and lighting the arch approach
    ...Array.from({ length: 8 }, (_, i) => {
      const a = i / 8 * Math.PI * 2 + 0.4;
      return { t: 'lamp', x: Math.cos(a) * 21.6, z: Math.sin(a) * 21.6 };
    }),
    { t: 'lamp', x: 11, z: ARCH_Z + 9 }, { t: 'lamp', x: -11, z: ARCH_Z + 9 },
    // chess tables — the SW corner by the MacDougal gate, where the hustlers sit
    ...Array.from({ length: 6 }, (_, i) => ({ t: 'chess', x: -64 + (i % 3) * 4.2, z: 46 + ((i / 3) | 0) * 4.6 })),
    { t: 'bin', x: 11, z: -30 }, { t: 'bin', x: -11, z: 26 }, { t: 'bin', x: 26, z: -9 },
    { t: 'bin', x: -27, z: 10 }, { t: 'bin', x: 6.5, z: -44 }, { t: 'bin', x: -6.5, z: -44 },
    { t: 'bin', x: 52, z: 52 }, { t: 'bin', x: -52, z: -52 },
    { t: 'bikerack', x: -19, z: -44 }, { t: 'bikerack', x: 19, z: -44 },
    { t: 'bikerack', x: -46, z: 12, rot: Math.PI / 2 },
    { t: 'cart', x: -24, z: -40, rot: 0.5 }, { t: 'cart', x: 25, z: 12, rot: -1.2 },
    // the Holley bust on the west side — small, easy to miss, like the real one
    { t: 'statue', x: -30, z: 3, rot: 1.9, text: 'HOLLEY', bust: true },
  ],

  landmarks: [
    { k: 'fountain',  x: 0,   z: 0 },
    { k: 'arch',      x: 0,   z: ARCH_Z },
    { k: 'row',       x: 0,   z: -109, len: 16 },              // Washington Square North
    { k: 'church',    x: -25, z: 111 },                        // Judson Memorial
    { k: 'library',   x: 126, z: 62 },                         // Bobst
    { k: 'garibaldi', x: 30,  z: 3, rot: -1.9 },
    { k: 'dogrun',    x: -62, z: 14, w: 24, d: 17 },
    { k: 'playground',x: 56,  z: 56, r: 17 },
  ],

  buildings: [
    { kind: 'ring', r: 112, count: 14, palette: [0x8d6e63, 0x9e9e9e, 0xa1887f, 0x90a4ae, 0xbcaaa4, 0x78909c],
      h: { n: [16, 50], e: [16, 50], w: [16, 50], s: [26, 81] }, tanks: true, shops: true,
      skip: [{ side: 'n', from: -96, to: 96 },     // The Row owns this frontage
             { side: 's', from: -46, to: -4 },     // Judson Church
             { side: 'e', from: 30,  to: 96 }] },  // Bobst
  ],

  flocks: [
    { x: 0, z: 0, r: 15, n: 7 }, { x: 6, z: -30, r: 7, n: 7 },
    { x: 0, z: ARCH_Z + 14, r: 8, n: 7 }, { x: -30, z: 26, r: 7, n: 7 },
  ],

  pen: { x: -62, z: 14, w: 24, d: 17 },
  lap: { x: 0, z: 0, rIn: 12, rOut: 33 },

  crowd: {
    wanderers: 28, pairs: 7, skaters: 6, benchSitters: 24, chillers: 15, clusters: 7,
    rim: { x: 0, z: 0, r: 11.6, rIn: 10.7, y: 1.48, n: 14 },
    seats: 8,
    knots: [{ x: -59, z: 51, n: 3 }],
    sellers: 1, sellerTag: '🌿',
    gates: [[0, ARCH_Z + 16], [-40, 40], [40, 40], [40, -40], [-14, ARCH_Z + 16], [40, 0], [-40, 0]],
    drums: [{ x: 17, z: 19 }, { x: -26, z: -14 }],
    buskers: { n: 3, x: 0, z: 0, r0: 15, r1: 19 },
    dogs: { n: 6, penned: 4 },
    // where people actually are: the plaza rim and the promenade carry the
    // density, the lawn interiors are nearly empty
    hotspots: [
      { shape: 'ring', x: 0, z: 0, r0: 21.5, r1: 35.5, p: 0.34 },
      { shape: 'rect', x: 0, z: -38, w: 13, d: 48, p: 0.18 },
      { shape: 'ring', x: 0, z: 0, r0: 26, r1: 88, p: 0.26 },
    ],
    reject: [{ x: 0, z: 0, r: 13 }],
  },

  places: [
    { n: 'under the arch',           x: 0,   z: ARCH_Z, r: 20 },
    { n: 'on the fountain rim',      x: 0,   z: 0,  r: 15 },
    { n: 'the fountain plaza',       x: 0,   z: 0,  r: 26 },
    { n: 'the Fifth Ave promenade',  x: 0,   z: -38, r: 26 },
    { n: 'by the dog run',           x: -62, z: 14, r: 18 },
    { n: 'the chess corner',         x: -60, z: 48, r: 16 },
    { n: 'by Garibaldi',             x: 30,  z: 3,  r: 12 },
    { n: 'at the drum circle',       x: 17,  z: 19, r: 12 },
    { n: 'at the drum circle',       x: -26, z: -14, r: 12 },
  ],
  quads: ['the north-west lawn', 'the north-east lawn', 'the south-west lawn', 'the south-east lawn'],
  areaCodes: ['212', '347', '646', '917'],

  quests: [
    { id:'wingman', name:'Rico', gender:'m', job:'nervous, by the fountain', at:{x:8,z:24}, rep:3,
      title:'Wingman', brief:"I've been standing here forty minutes working up to saying hi to somebody. You do it. Get a number, any number, and prove it's possible.",
      thanks:"You actually did it. Okay. Okay, I'm going in.",
      steps:[{ text:"Get somebody's number", count:'number', n:1 }] },
    { id:'lostdog', name:'Nadia', gender:'f', job:'dog run regular', at:{x:-62,z:25}, rep:3,
      title:'One dog short', brief:"Somebody left the gate open. There's a dog loose in the park and it will not come to me. Go get it and walk it back in here.",
      thanks:"You got it. Look at that — straight back to causing problems in here.",
      steps:[{ text:'Find the loose dog', custom:'dogFind' }, { text:'Lead it into the dog run', custom:'dogHome' }] },
    { id:'crowd', name:'Otis', gender:'m', job:'busker', at:{x:-21,z:25}, rep:3,
      title:'Pull a crowd', brief:"Nobody stops for a man playing to an empty patch of stone. Go round up three people and point them this way.",
      thanks:"Three heads. That's a crowd, and a crowd makes a crowd. We're in business.",
      steps:[{ text:'Invite three people over (talk, then invite)', count:'invite', n:3 }] },
    { id:'beat', name:'Kwame', gender:'m', job:'drummer', at:{x:13,z:24}, rep:2,
      title:'Hold the circle', brief:"Circle's thinning out. Stand in it for twenty seconds — just be a body in the ring, that's how these things survive.",
      thanks:"That's it. You held it. That's all it ever takes.",
      steps:[{ text:'Stay in the east drum circle for 20s', hold:{ x:17, z:19, r:7, t:20 } }] },
    { id:'lap', name:'Sasha', gender:'f', job:'skater', at:{x:23,z:-7}, rep:4,
      title:'Fountain lap', brief:"Whole lap of the fountain, on the board, without putting a foot down like a tourist. Under forty-five seconds or it doesn't count.",
      thanks:"Clean. You can roll with us any time.",
      steps:[{ text:'Skate a full lap of the fountain plaza', custom:'lap' }] },
    { id:'pigeons', name:'Gus', gender:'m', job:'been here since 1971', at:{x:35,z:23}, rep:2,
      title:'Put them up', brief:"Best thing in this park is a hundred pigeons going up at once. My knees don't do it any more. Run at six flocks for me.",
      thanks:"Six. I heard every one of them from here. Good.",
      steps:[{ text:'Scatter six flocks of pigeons', count:'flush', n:6 }] },
    { id:'tour', name:'Greta', gender:'f', job:'first day in the city', at:{x:9,z:-56}, rep:3,
      title:'The whole square', brief:"I have one afternoon and a list. Walk it with me — the arch, Garibaldi, the chess tables, the dog run. Then tell me what I actually saw.",
      thanks:"All four. Now I've been to New York. Thank you, honestly.",
      steps:[{ text:'The arch', reach:{ x:0, z:-63, r:11 } }, { text:'Garibaldi', reach:{ x:30, z:3, r:9 } },
             { text:'The chess corner', reach:{ x:-60, z:48, r:11 } }, { text:'The dog run', reach:{ x:-62, z:14, r:14 } }] },
    { id:'golden', name:'Anais', gender:'f', job:'photographer', at:{x:-9,z:-52}, rep:2,
      title:'Golden hour', brief:"I need a person under that arch when the light goes gold, and everyone I asked said no. Press T until the sun's low, then stand under it.",
      thanks:"Got it. The light did all the work and I'll take all the credit.",
      steps:[{ text:'Stand under the arch at golden hour (T cycles time)', reach:{ x:0, z:-63, r:12 }, when:'golden' }] },
    { id:'coffee', name:'Sol', gender:'m', job:'cart, north-east gate', at:{x:37,z:36}, rep:2,
      title:'Cold coffee', brief:"The old guy at the chess tables has been waiting on this cup for an hour. Take it to him before it's iced coffee by accident.",
      thanks:"He tipped me for it, which never happens. Here — you did the walking.",
      steps:[{ text:'Carry the coffee to the chess corner', reach:{ x:-60, z:48, r:8 } }] },
  ],
};
