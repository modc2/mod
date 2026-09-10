// ---------------------------------------------------------------------------
// freewash-fork — the map registry
// ---------------------------------------------------------------------------
// A map is DATA, not code. Everything that makes one place different from
// another — where the walks run, what gets planted along them, which buildings
// line the street, who is standing where and what they want from you — lives in
// a plain object with no reference to three.js. The engine in index.html is the
// only thing that knows how to turn one of these into geometry, and the editor
// in editor.js is the only thing that knows how to draw one on a grid.
//
// That split is the whole feature: a user-made map is exactly as real to the
// engine as Kensington is, because Kensington is one of these too.
//
// SCHEMA (everything optional except id/name/size/bound)
// ---------------------------------------------------------------------------
//  id, name, city, tag, blurb, icon, accent       identity + start-screen card
//  size          half-extent of the ground plane, metres
//  bound         half-extent of the playable box (hero is clamped to it)
//  spawn         {x, z, face}  where you enter, and the R-key reset
//  ground        'grass' | 'sand' | 'asphalt' | 'gravel' | 'brick' | 'dirt'
//  outer         what lies beyond `size`: 'asphalt' | 'sand' | 'ocean' | 'none'
//  wear          draw the big non-tiling grime/desire-path overlay (default on)
//  time          default hour preset: dawn|morning|noon|golden|dusk|night
//  weather       'clear' | 'haze' | 'marine'   (fog + exposure trim)
//
//  surfaces[]    {k:kind, x, z, w, d, rot, r, shape:'rect'|'circle', paved}
//                kinds: pavement sand grass gravel asphalt water boardwalk
//                       brick dirt court cobble tarmacPark
//  walks[]       {p:[[x,z],…], w} or {bow:[[x,z],[x,z],amount], w, seg}
//  roads[]       {p:[[x,z],…], w, sidewalk, lines:'dash'|'double'|'none',
//                 tram:bool, name}
//  keepOut[]     {x,z,r} or {x,z,w,d}   — nothing self-plants or spawns here
//
//  planting      { allee:{every, back, kinds[]} | null,
//                  groves:{n, kinds:{kind:weight}, minGap},
//                  rows:[{p:[[x,z],…], every, kind, off, jitter}],
//                  specials:[{x,z,kind,scale}] }
//                tree kinds: plane honey elm maple oak cherry palm pine ficus
//  furnish       { every, lampEvery, bench:bool } | null
//
//  props[]       {t:type, x, z, rot, …}   — see PROP_TYPES below
//  landmarks[]   {k:kind, x, z, rot, …}   — see LANDMARK_KINDS below
//  buildings[]   {kind:'row'|'block'|'ring', …}
//
//  flocks[]      {x, z, r, n, species:'pigeon'|'gull'}
//  crowd         { wanderers, pairs, skaters, benchSitters, chillers, clusters,
//                  sellers, sellerTag, gates:[[x,z]], drums:[{x,z}],
//                  buskers:[{x,z}] | n, rim:{x,z,r,y,n},
//                  dogs:{n, penned}, hotspots:[{shape, …, w}] }
//  pen           {x,z,w,d}  the dog run, if the map has one
//  lap           {x,z,rIn,rOut}  where the skate-lap quest is measured
//  places[]      {n:'name', x, z, r}   — reverse geocode for the contact list
//  quests[]      the sidequest list (same objective vocabulary on every map)
//  music         'drums' | 'street' | 'surf'   — which ambience the synth plays
// ---------------------------------------------------------------------------

import kensington from './kensington.js';

export const MAP_VERSION = 2;
export const USER_KEY = 'freewash-fork.maps.v1';

export const BUILTIN = [kensington];

// What the editor offers, and what the engine knows how to build. Keeping the
// vocabulary in one exported table means the editor's palette can never drift
// from what the engine actually implements.
export const PROP_TYPES = [
  { t:'bench',    label:'Bench',        icon:'🪑', rot:true,  seat:true },
  { t:'lamp',     label:'Lamppost',     icon:'💡' },
  { t:'bin',      label:'Litter bin',   icon:'🗑' },
  { t:'bikerack', label:'Bike rack',    icon:'🚲', rot:true },
  { t:'cart',     label:'Hot dog cart', icon:'🌭', rot:true },
  { t:'chess',    label:'Chess table',  icon:'♟',  rot:true, seat:true },
  { t:'stall',    label:'Market stall', icon:'⛺', rot:true, opts:['goods','color'] },
  { t:'crates',   label:'Produce crates', icon:'🍊', rot:true },
  { t:'rack',     label:'Clothes rack', icon:'👕', rot:true },
  { t:'planter',  label:'Planter',      icon:'🪴' },
  { t:'bollard',  label:'Bollard',      icon:'🔸' },
  { t:'phonebox', label:'Phone box',    icon:'📞', rot:true },
  { t:'busshelter', label:'Bus shelter', icon:'🚏', rot:true, seat:true },
  { t:'car',      label:'Parked car',   icon:'🚗', rot:true, opts:['color'] },
  { t:'van',      label:'Parked van',   icon:'🚐', rot:true, opts:['color'] },
  { t:'bike',     label:'Locked bike',  icon:'🚲', rot:true },
  { t:'umbrella', label:'Beach umbrella', icon:'⛱', opts:['color'] },
  { t:'towel',    label:'Beach towel',  icon:'🏖', rot:true },
  { t:'surfrack', label:'Surfboard rack', icon:'🏄', rot:true },
  { t:'volley',   label:'Volleyball net', icon:'🏐', rot:true },
  { t:'palmpot',  label:'Potted palm',  icon:'🌴' },
  { t:'picnic',   label:'Picnic table', icon:'🧺', rot:true, seat:true },
  { t:'firepit',  label:'Fire pit',     icon:'🔥' },
  { t:'sign',     label:'Street sign',  icon:'🪧', rot:true, opts:['text'] },
  { t:'mural',    label:'Mural wall',   icon:'🎨', rot:true, opts:['text','color'] },
  { t:'statue',   label:'Statue',       icon:'🗿', rot:true, opts:['text'] },
  { t:'boat',     label:'Narrowboat',   icon:'🛶', rot:true, opts:['color'] },
  { t:'stage',    label:'Busker stage', icon:'🎤', rot:true },
  { t:'hoop',     label:'Basketball hoop', icon:'🏀', rot:true },
  { t:'tent',     label:'Food tent',    icon:'🍲', rot:true, opts:['color'] },
  { t:'flag',     label:'Flagpole',     icon:'🚩' },
  { t:'fountain2',label:'Drinking fountain', icon:'⛲' },
];

export const LANDMARK_KINDS = [
  { k:'fountain',   label:'Wading pool',         icon:'⛲', r:12 },
  { k:'arch',       label:'Marble arch',         icon:'🏛', r:11, rot:true },
  { k:'row',        label:'Terrace row',         icon:'🏘', rot:true, opts:['len'] },
  { k:'church',     label:'Church + campanile',  icon:'⛪', rot:true },
  { k:'library',    label:'Big library block',   icon:'📚', rot:true },
  { k:'statue',     label:'Bronze figure',       icon:'🗿', rot:true, opts:['text'] },
  { k:'dogrun',     label:'Dog run',             icon:'🐕', opts:['w','d'] },
  { k:'playground', label:'Playground',          icon:'🛝', opts:['r'] },
  { k:'synagogue',  label:'Domed synagogue',     icon:'🕍', rot:true },
  { k:'gardencar',  label:'Garden car',          icon:'🚙', rot:true },
  { k:'streetcar',  label:'Streetcar',           icon:'🚋', rot:true, opts:['len'] },
  { k:'marketgate', label:'Market gateway sign', icon:'🪧', rot:true, opts:['text'] },
  { k:'lock',       label:'Canal lock',          icon:'🚪', rot:true },
  { k:'railbridge', label:'Iron rail bridge',    icon:'🌉', rot:true, opts:['text','len'] },
  { k:'roundhouse', label:'Roundhouse',          icon:'🎪' },
  { k:'stables',    label:'Brick market arches', icon:'🧱', rot:true, opts:['len'] },
  { k:'bigsign',    label:'Giant 3D shop sign',  icon:'👢', rot:true, opts:['text'] },
  { k:'pier',       label:'Ocean pier',          icon:'🎡', rot:true, opts:['len'] },
  { k:'ferris',     label:'Ferris wheel',        icon:'🎡' },
  { k:'coaster',    label:'Roller coaster',      icon:'🎢', rot:true },
  { k:'lifeguard',  label:'Lifeguard tower',     icon:'🛟', rot:true },
  { k:'muscle',     label:'Muscle beach rig',    icon:'💪', rot:true },
  { k:'pierarch',   label:'Neon entry arch',     icon:'🌈', rot:true, opts:['text'] },
  { k:'bandshell',  label:'Bandshell',           icon:'🎶', rot:true },
  { k:'clocktower', label:'Clock tower',         icon:'🕰' },
];

export const GROUND_KINDS = ['grass','sand','asphalt','gravel','brick','dirt'];
export const SURFACE_KINDS = ['pavement','sand','grass','gravel','asphalt','water',
                              'boardwalk','brick','dirt','court','cobble'];
export const TREE_KINDS = ['plane','honey','elm','maple','oak','cherry','palm','pine','ficus'];

// --- defaults ---------------------------------------------------------------
// Anything a map leaves out gets filled in here, so a two-line map still runs.
export const MAP_DEFAULTS = {
  city: '', tag: '', blurb: '', icon: '🗺', accent: '#a78bfa',
  size: 110, bound: 96,
  spawn: { x: 0, z: 0, face: 0 },
  ground: 'grass', outer: 'asphalt', wear: true,
  time: 'golden', weather: 'clear',
  surfaces: [], walks: [], roads: [], keepOut: [],
  planting: null, furnish: null,
  props: [], landmarks: [], buildings: [],
  flocks: [], places: [], quests: [],
  pen: null, lap: null, plaza: null,
  music: 'drums',
  crowd: {
    wanderers: 24, pairs: 6, skaters: 4, benchSitters: 18, chillers: 8,
    clusters: 6, sellers: 0, sellerTag: '🌿', gates: [], drums: [], buskers: 0,
    rim: null, dogs: { n: 3, penned: false }, hotspots: [],
  },
};

const CROWD_DEFAULTS = MAP_DEFAULTS.crowd;

// Deep-ish merge: one level for `crowd`/`spawn`, replace for everything else.
// Maps are authored as whole objects, so a real deep merge would only make it
// harder to say "no trees here" by writing `planting: null`.
export function normalizeMap(raw) {
  const m = Object.assign({}, MAP_DEFAULTS, raw || {});
  m.spawn = Object.assign({}, MAP_DEFAULTS.spawn, raw && raw.spawn);
  m.crowd = Object.assign({}, CROWD_DEFAULTS, raw && raw.crowd);
  m.crowd.dogs = Object.assign({}, CROWD_DEFAULTS.dogs, raw && raw.crowd && raw.crowd.dogs);
  for (const k of ['surfaces','walks','roads','keepOut','props','landmarks',
                   'buildings','flocks','places','quests'])
    if (!Array.isArray(m[k])) m[k] = [];
  m.size = Math.max(40, +m.size || 110);
  m.bound = Math.max(20, Math.min(m.size - 4, +m.bound || m.size - 14));
  return m;
}

// --- user maps (localStorage) ----------------------------------------------
// Kept in one blob rather than a key per map: the whole library is small, and
// one read means the picker and the editor can never see different sets.
export function loadUserMaps() {
  try {
    const raw = JSON.parse(localStorage.getItem(USER_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter(m => m && m.id) : [];
  } catch { return []; }
}
export function saveUserMap(map) {
  const all = loadUserMaps();
  const i = all.findIndex(m => m.id === map.id);
  map.updated = new Date().toISOString().slice(0, 10);
  if (i >= 0) all[i] = map; else all.push(map);
  localStorage.setItem(USER_KEY, JSON.stringify(all));
  return map;
}
export function deleteUserMap(id) {
  localStorage.setItem(USER_KEY, JSON.stringify(loadUserMaps().filter(m => m.id !== id)));
}
export function userMapId(name) {
  const base = 'user:' + String(name || 'map').toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '').slice(0, 28) || 'user:map';
  const taken = new Set(loadUserMaps().map(m => m.id));
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) if (!taken.has(base + '-' + i)) return base + '-' + i;
}

// Every map the picker should show, built-ins first.
export function listMaps() {
  return [...BUILTIN, ...loadUserMaps()];
}
export function findMap(id) {
  if (!id) return null;
  return listMaps().find(m => m.id === id) || null;
}

// The map you get when you press "Create a map": an empty green square with a
// path across it, which is enough to walk around in while you build.
export function blankMap(name = 'My map') {
  return {
    id: userMapId(name), name, city: 'somewhere', tag: 'made in the editor',
    icon: '✏️', accent: '#7dd3fc',
    blurb: 'A map made in the freewash-fork editor. Draw the walks, plant the trees, ' +
           'line the streets with shops, then walk around in it.',
    size: 110, bound: 96, ground: 'grass', outer: 'asphalt',
    spawn: { x: 0, z: 30, face: Math.PI },
    walks: [{ p: [[0, 90], [0, 0], [0, -90]], w: 8 }, { p: [[-90, 0], [90, 0]], w: 8 }],
    planting: { allee: { every: 12, back: 3.2, kinds: ['plane'] },
                groves: { n: 60, kinds: { plane: 0.8, honey: 0.2 } } },
    furnish: { every: 14, lampEvery: 3, bench: true },
    surfaces: [], roads: [], props: [], landmarks: [], buildings: [],
    keepOut: [], places: [], quests: [],
    flocks: [{ x: 0, z: 0, r: 10, n: 7, species: 'pigeon' }],
    crowd: { wanderers: 18, pairs: 4, skaters: 3, benchSitters: 12, chillers: 6,
             clusters: 4, buskers: 1, dogs: { n: 2 },
             hotspots: [{ shape: 'ring', x: 0, z: 0, r0: 8, r1: 40, w: 0.5 }] },
    music: 'drums',
  };
}
