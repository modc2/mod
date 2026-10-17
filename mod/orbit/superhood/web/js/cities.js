/* cities.js — the cities SUPER HOOD BROS. can be played in.
 *
 * A city is a reskin, not a new map: the three courses keep their layout and
 * their physics, and the city renames them, repaints the tile themes, recolours
 * the skyline and picks the landmark you see behind the park. Brooklyn is the
 * original and overrides nothing.
 *
 * Fields
 *   name       what the title card and the picker call it
 *   levels     a name for every course id in levels.js (the HUD shows these)
 *   theme      per-theme colour overrides, merged over game.js's base THEME
 *   skyline    [far, near] parallax building colours
 *   title      [top, middle, bottom] title-card gradient stops
 *   landmark   { kind: bank|spire|clock|lattice|column, ...colours }
 *
 * The bitmap font is A-Z, 0-9 and a little punctuation: no accents.
 */
(function (global) {
  'use strict';

  var LIST = [
    {
      id: 'brooklyn', name: 'BROOKLYN',
      levels: { '1-1': 'PARK SLOPE', 'cellar': 'TOKEN CELLAR',
                '1-2': 'G TRAIN', '1-3': 'WASHINGTON PARK' },
      theme: {},
      skyline: ['#7f8fbf', '#5a6a9a'],
      title: ['#101538', '#3b3f7a', '#c86a3a'],
      landmark: { kind: 'bank' }
    },
    {
      id: 'chicago', name: 'CHICAGO',
      levels: { '1-1': 'WICKER PARK', 'cellar': 'LOWER WACKER',
                '1-2': 'BLUE LINE', '1-3': 'GRANT PARK' },
      theme: {
        slope:  { sky: ['#6b8fc4', '#b9cfe6'], brick: '#8e3b2a', brickDark: '#5c2216' },
        park:   { sky: ['#5f8fd0', '#c4dcf0'], top: '#3f9a4a', topEdge: '#6cc46a' },
        tunnel: { brick: '#2f4f7a', brickDark: '#1c3150', mortar: '#a8c0e0' }
      },
      skyline: ['#7d8aa6', '#55617f'],
      title: ['#0d1b2a', '#34507a', '#d98b4a'],
      landmark: { kind: 'spire', body: '#26262e', trim: '#3c3c48' }
    },
    {
      id: 'london', name: 'LONDON',
      levels: { '1-1': 'CAMDEN TOWN', 'cellar': 'OYSTER VAULT',
                '1-2': 'NORTHERN LINE', '1-3': 'HYDE PARK' },
      theme: {
        slope:  { sky: ['#8a9bb0', '#d0d8e0'], brick: '#b8955a', brickDark: '#7d6236',
                  mortar: '#ece2c8' },
        park:   { sky: ['#8296b0', '#d4dde6'], top: '#4c9a40', topEdge: '#78c062' },
        tunnel: { brick: '#7a2a2a', brickDark: '#4e1616', mortar: '#e0c8c8' }
      },
      skyline: ['#8a93a3', '#6a7384'],
      title: ['#141824', '#3c4660', '#b07a52'],
      landmark: { kind: 'clock', body: '#9a8858', trim: '#6e6038', face: '#f0e8c8' }
    },
    {
      id: 'tokyo', name: 'TOKYO',
      levels: { '1-1': 'SHIMOKITAZAWA', 'cellar': 'PASMO VAULT',
                '1-2': 'GINZA LINE', '1-3': 'UENO PARK' },
      theme: {
        slope:  { sky: ['#6fa8ff', '#ffd6ea'], brick: '#9a9aa6', brickDark: '#6a6a78',
                  mortar: '#e4e4ee' },
        park:   { sky: ['#78a8ff', '#ffd1e8'], top: '#5fb04a', topEdge: '#ffb7d5' },
        tunnel: { brick: '#a8761a', brickDark: '#6e4c0e', mortar: '#f0dca8' }
      },
      skyline: ['#8e86b8', '#665e94'],
      title: ['#1a0f2e', '#5a2f6e', '#ff7a6a'],
      landmark: { kind: 'lattice', body: '#e8482a', trim: '#f4f4f4' }
    },
    {
      id: 'cdmx', name: 'MEXICO CITY',
      levels: { '1-1': 'LA CONDESA', 'cellar': 'BOVEDA',
                '1-2': 'LINEA 1', '1-3': 'CHAPULTEPEC' },
      theme: {
        slope:  { sky: ['#3f8fe0', '#a8dcff'], brick: '#c0623a', brickDark: '#8a3e20',
                  mortar: '#f0d8a8' },
        park:   { sky: ['#4890e0', '#b0e0ff'], top: '#3e9e3a', topEdge: '#74cc58' },
        tunnel: { brick: '#b0306a', brickDark: '#741c44', mortar: '#f0c0d8' }
      },
      skyline: ['#a08a7a', '#7a6656'],
      title: ['#14102a', '#6a3a5a', '#f0a040'],
      landmark: { kind: 'column', body: '#d8d0c0', trim: '#a89c88', cap: '#f2c230' }
    }
  ];

  var BY_ID = {};
  LIST.forEach(function (c) { BY_ID[c.id] = c; });

  global.CITIES = {
    list: LIST,
    ids: function () { return LIST.map(function (c) { return c.id; }); },
    get: function (id) { return BY_ID[id] || LIST[0]; },
    has: function (id) { return !!BY_ID[id]; },
    // a step left or right through the list, wrapping
    next: function (id, d) {
      var i = LIST.indexOf(BY_ID[id] || LIST[0]);
      return LIST[(i + d + LIST.length) % LIST.length].id;
    }
  };
})(window);
