// Kensington Market, Toronto — the map freewash-fork ships.
//
// Laid out to the real street plan. Four north–south streets (Bellevue,
// Augusta, Kensington, Spadina) crossed by five east–west ones (College,
// Oxford, Baldwin, Nassau, St Andrew), with Bellevue Square dropped into the
// block between Nassau and St Andrew. Augusta is the spine and the widest;
// Kensington Avenue is the vintage block; Baldwin is where you eat; Spadina is
// four lanes and a streetcar. Everything else in the engine — the sidewalks,
// the shopfronts, the hydro poles, the street trees, the parked cars, the
// crowd — is placed off those centrelines, which is why the place reads as a
// neighbourhood rather than a scatter.
//
// NOTE: this file is the map as DATA. The engine in ../index.html currently
// builds Kensington directly from the same numbers rather than reading this
// object; the two are kept in step by hand. See ./index.js for the schema.
const AUGUSTA = -26, KENSINGTON = 24, BELLEVUE_AVE = -78, SPADINA = 84;
const COLLEGE = -88, OXFORD = -50, BALDWIN = -4, NASSAU = 40, ST_ANDREW = 72;
const SQUARE = { x: -52, z: 56, w: 38, d: 22 };

export default {
  id: 'kensington',
  name: 'Kensington Market',
  city: 'Toronto',
  tag: 'live from toronto',
  icon: '🍅',
  accent: '#e0503f',
  blurb: 'Walk or skate Augusta, Baldwin and Kensington Avenue — a hundred and fifty Victorian ' +
         'houses painted every colour there is, fruit stacked to head height in the doorways, ' +
         'vintage on the rails under the awnings, murals on every blank wall and the whole sky ' +
         'cut into pieces by the hydro wires. Drums in Bellevue Square, a busker on the corner, ' +
         'the car full of plants still parked where somebody left it in 1994.',
  size: 110, bound: 96,
  spawn: { x: AUGUSTA, z: BALDWIN + 16, face: Math.PI },
  ground: 'asphalt', outer: 'asphalt', wear: true,
  time: 'golden', weather: 'haze', music: 'street',

  roads: [
    { p: [[BELLEVUE_AVE,-110],[BELLEVUE_AVE,110]], w: 8,  sidewalk: 3.0, lines:'dash', name:'Bellevue Ave' },
    { p: [[AUGUSTA,-110],[AUGUSTA,110]],           w: 10.8, sidewalk: 4.4, lines:'dash', name:'Augusta Ave' },
    { p: [[KENSINGTON,-110],[KENSINGTON,110]],     w: 9.2, sidewalk: 4.0, lines:'dash', name:'Kensington Ave' },
    { p: [[SPADINA,-110],[SPADINA,110]],           w: 23, sidewalk: 4.5, lines:'double', tram: true, name:'Spadina Ave' },
    { p: [[-110,COLLEGE],[110,COLLEGE]],           w: 17, sidewalk: 4.0, lines:'double', name:'College St' },
    { p: [[-110,OXFORD],[110,OXFORD]],             w: 8,  sidewalk: 3.2, lines:'dash', name:'Oxford St' },
    { p: [[-110,BALDWIN],[110,BALDWIN]],           w: 10, sidewalk: 4.4, lines:'dash', name:'Baldwin St' },
    { p: [[-110,NASSAU],[110,NASSAU]],             w: 8,  sidewalk: 3.4, lines:'dash', name:'Nassau St' },
    { p: [[-110,ST_ANDREW],[110,ST_ANDREW]],       w: 8,  sidewalk: 3.4, lines:'dash', name:'St Andrew St' },
  ],

  // Bellevue Square: the one piece of grass, with paths cut across it
  surfaces: [
    { k: 'grass', x: SQUARE.x, z: SQUARE.z, w: SQUARE.w, d: SQUARE.d },
    { k: 'sand',  x: -37, z: 61, r: 7.5, shape: 'circle' },              // playground
    { k: 'dirt',  x: -65, z: 60, w: 12, d: 12 },                         // off-leash corner
  ],
  walks: [
    { p: [[SQUARE.x-19, SQUARE.z-11],[SQUARE.x-6, SQUARE.z-2],[SQUARE.x+19, SQUARE.z+11]], w: 3.4 },
    { p: [[SQUARE.x-19, SQUARE.z+11],[SQUARE.x-4, SQUARE.z+3],[SQUARE.x+19, SQUARE.z-11]], w: 3.0 },
    { p: [[SQUARE.x-21, SQUARE.z],[SQUARE.x+21, SQUARE.z]], w: 3.6 },
  ],
  keepOut: [
    { x: -65, z: 60, w: 16, d: 16 },        // off-leash corner
    { x: -37, z: 61, r: 9 },                // playground
    { x: -44, z: 53, r: 8 },                // wading pool
    { x: -58, z: 50, r: 3.5 },              // Al Waxman
    { x: -56, z: 62, r: 6 },                // the drum circle
  ],

  planting: {
    rows: [{ p: [[AUGUSTA-9.9,-96],[AUGUSTA-9.9,96]], every: 17, kind: 'maple' },
           { p: [[KENSINGTON-8.6,-96],[KENSINGTON-8.6,96]], every: 17, kind: 'maple' },
           { p: [[-96,BALDWIN-6.8],[96,BALDWIN-6.8]], every: 17, kind: 'honey' }],
    groves: { n: 34, kinds: { maple: 0.6, honey: 0.4 }, minGap: 6.2 },
    specials: [{ x: SQUARE.x-12, z: SQUARE.z-7, kind: 'elm', scale: 0.86 },
               { x: SQUARE.x+11, z: SQUARE.z+6, kind: 'elm', scale: 0.72 }],
  },
  furnish: { every: 7.5, lampEvery: 4, bench: true },

  landmarks: [
    { k: 'marketgate', x: AUGUSTA, z: -15, text: 'KENSINGTON MARKET' },
    { k: 'gardencar',  x: AUGUSTA - 4.1, z: -28, rot: 0 },
    { k: 'synagogue',  x: -74, z: 84 },                          // the Kiever, Denison Sq
    { k: 'statue',     x: -58, z: 50, rot: -0.5, text: 'AL WAXMAN' },
    { k: 'streetcar',  x: SPADINA - 4.2, z: 8, len: 22 },
    { k: 'church',     x: -108, z: -34 },                        // St Stephen-in-the-Fields
    { k: 'dogrun',     x: -65, z: 60, w: 12, d: 12 },
    { k: 'playground', x: -37, z: 61, r: 7.5 },
    { k: 'fountain',   x: -44, z: 53, r: 4.6 },                  // the wading pool
  ],

  buildings: [
    // shop rows: one per street frontage, skipping every intersection
    { kind: 'row', along: 'z', at: AUGUSTA - 9.8,      facing: '+x', mix: ['food','food','other','vintage'] },
    { kind: 'row', along: 'z', at: AUGUSTA + 9.8,      facing: '-x', mix: ['food','other','vintage'] },
    { kind: 'row', along: 'z', at: KENSINGTON - 8.6,   facing: '+x', mix: ['vintage','vintage','other'] },
    { kind: 'row', along: 'z', at: KENSINGTON + 8.6,   facing: '-x', mix: ['vintage','vintage','food','other'] },
    { kind: 'row', along: 'x', at: BALDWIN - 9.4,      facing: '+z', mix: ['food','food','other'] },
    { kind: 'row', along: 'x', at: BALDWIN + 9.4,      facing: '-z', mix: ['food','other'] },
    { kind: 'row', along: 'x', at: NASSAU - 7.4,       facing: '+z', mix: ['other','food'] },
    { kind: 'row', along: 'x', at: OXFORD + 7.2,       facing: '-z', mix: ['other','vintage'] },
    { kind: 'row', along: 'x', at: OXFORD - 7.2,       facing: '+z', mix: ['other','food'] },
    { kind: 'row', along: 'x', at: ST_ANDREW - 7.4,    facing: '+z', mix: ['other','food'] },
    { kind: 'row', along: 'z', at: BELLEVUE_AVE - 7,   facing: '+x', mix: ['other'] },
    { kind: 'row', along: 'z', at: BELLEVUE_AVE + 7,   facing: '-x', mix: ['other','vintage'] },
    { kind: 'row', along: 'z', at: SPADINA - 16,       facing: '+x', mix: ['other','food'] },
    { kind: 'ring', r: 118, count: 15, palette: [0x8d6e63, 0x9e9e9e, 0xa1887f, 0x90a4ae, 0xbcaaa4, 0x78909c, 0x8a5a4a],
      h: { n: [13,37], w: [13,37], e: [20,66], s: [20,66] }, shops: true },
  ],

  flocks: [
    { x: AUGUSTA, z: BALDWIN, r: 7, n: 7 }, { x: AUGUSTA, z: -34, r: 6, n: 7 },
    { x: AUGUSTA, z: 26, r: 6, n: 7 },      { x: KENSINGTON, z: BALDWIN+14, r: 6, n: 7 },
    { x: KENSINGTON, z: -20, r: 6, n: 7 },  { x: SQUARE.x+6, z: SQUARE.z-4, r: 8, n: 7 },
    { x: SQUARE.x-10, z: SQUARE.z+5, r: 7, n: 7 }, { x: -6, z: BALDWIN, r: 6, n: 7 },
    { x: KENSINGTON+22, z: NASSAU, r: 6, n: 7 },
  ],

  pen: { x: -65, z: 60, w: 12, d: 12 },
  lap: { x: SQUARE.x, z: SQUARE.z, rIn: 13, rOut: 32 },

  crowd: {
    wanderers: 34, pairs: 9, skaters: 6, benchSitters: 16, chillers: 10, clusters: 10,
    sellers: 26, sellerTag: '🍊',
    drums: [{ x: -56, z: 62 }],
    buskers: [{ x: AUGUSTA+7.6, z: BALDWIN-11 }, { x: AUGUSTA-7.6, z: 22 },
              { x: KENSINGTON+7, z: -16 }, { x: SQUARE.x+12, z: SQUARE.z-6 }],
    dogs: { n: 6, penned: 4 },
    // where people actually are: Augusta and Baldwin carry almost all of it
    hotspots: [
      { shape:'rect', x: AUGUSTA,      z: 0,        w: 16, d: 130, p: 0.30 },
      { shape:'rect', x: 0,            z: BALDWIN,  w: 96, d: 14,  p: 0.18 },
      { shape:'rect', x: KENSINGTON,   z: -10,      w: 14, d: 120, p: 0.17 },
      { shape:'rect', x: SQUARE.x,     z: SQUARE.z, w: 34, d: 18,  p: 0.12 },
      { shape:'rect', x: 0,            z: NASSAU,   w: 90, d: 12,  p: 0.06 },
      { shape:'rect', x: 0,            z: OXFORD,   w: 90, d: 12,  p: 0.06 },
      { shape:'rect', x: BELLEVUE_AVE, z: 10,       w: 12, d: 120, p: 0.05 },
      { shape:'rect', x: SPADINA-14,   z: 0,        w: 12, d: 140, p: 0.06 },
    ],
  },

  places: [
    { n: 'on Augusta',            x: AUGUSTA, z: 0, r: 16 },
    { n: 'on Kensington Ave',     x: KENSINGTON, z: -10, r: 14 },
    { n: 'on Baldwin',            x: 0, z: BALDWIN, r: 14 },
    { n: 'on Nassau',             x: 0, z: NASSAU, r: 12 },
    { n: 'on St Andrew',          x: 0, z: ST_ANDREW, r: 12 },
    { n: 'on Spadina',            x: SPADINA, z: 0, r: 16 },
    { n: 'in Bellevue Square',    x: SQUARE.x, z: SQUARE.z, r: 20 },
    { n: 'at the off-leash corner', x: -65, z: 60, r: 9 },
    { n: 'by the wading pool',    x: -44, z: 53, r: 9 },
    { n: 'by the garden car',     x: AUGUSTA-4.1, z: -28, r: 12 },
    { n: 'under the market sign', x: AUGUSTA, z: -15, r: 12 },
    { n: 'outside the Kiever',    x: -74, z: 84, r: 20 },
    { n: 'at the drum circle',    x: -56, z: 62, r: 11 },
  ],
  areaCodes: ['416', '647', '437'],

  quests: [
    { id:'wingman', name:'Marco', gender:'m', job:'nervous, outside the coffee place',
      at:{x:AUGUSTA+7,z:BALDWIN+9}, rep:3, title:'Wingman',
      steps:[{ text:"Get somebody's number", count:'number', n:1 }] },
    { id:'lostdog', name:'Nadia', gender:'f', job:'off-leash corner regular',
      at:{x:-63,z:50}, rep:3, title:'One dog short',
      steps:[{ text:'Find the loose dog', custom:'dogFind' },
             { text:'Lead it into the off-leash corner', custom:'dogHome' }] },
    { id:'crowd', name:'Ozzie', gender:'m', job:'busker, Augusta & Baldwin',
      at:{x:AUGUSTA+5.2,z:BALDWIN-9}, rep:3, title:'Pull a crowd',
      steps:[{ text:'Invite three people over', count:'invite', n:3 }] },
    { id:'beat', name:'Kwame', gender:'m', job:'drummer, Bellevue Square',
      at:{x:-52,z:66}, rep:2, title:'Hold the circle',
      steps:[{ text:'Stay in the drum circle for 20s', hold:{ x:-56, z:62, r:7, t:20 } }] },
    { id:'lap', name:'Sasha', gender:'f', job:'skater', at:{x:AUGUSTA+7,z:40}, rep:4,
      title:'Round the square', steps:[{ text:'Skate a full lap around Bellevue Square', custom:'lap' }] },
    { id:'pigeons', name:'Gus', gender:'m', job:'been on this street since 1971',
      at:{x:-39,z:63}, rep:2, title:'Put them up',
      steps:[{ text:'Scatter six flocks of pigeons', count:'flush', n:6 }] },
    { id:'tour', name:'Greta', gender:'f', job:'first day in the city',
      at:{x:AUGUSTA-7.4,z:-28}, rep:3, title:'The whole market',
      steps:[{ text:'The market sign on Augusta', reach:{ x:AUGUSTA, z:-15, r:9 } },
             { text:'The garden car', reach:{ x:AUGUSTA-1.1, z:-28, r:8 } },
             { text:'The Kiever', reach:{ x:-74, z:70, r:12 } },
             { text:'Bellevue Square', reach:{ x:SQUARE.x, z:SQUARE.z, r:13 } }] },
    { id:'golden', name:'Anaïs', gender:'f', job:'photographer',
      at:{x:AUGUSTA+7.4,z:-8}, rep:2, title:'Golden hour',
      steps:[{ text:'Stand under the market sign at golden hour', reach:{ x:AUGUSTA, z:-15, r:9 }, when:'golden' }] },
    { id:'coffee', name:'Sol', gender:'m', job:'behind the coffee window',
      at:{x:AUGUSTA-7.4,z:BALDWIN+16}, rep:2, title:'Cold coffee',
      steps:[{ text:'Carry the coffee to Kensington Ave', reach:{ x:KENSINGTON, z:-16, r:9 } }] },
  ],
};
