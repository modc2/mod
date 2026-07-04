// Named, pinnable trader-leaderboard filter combos (e.g. "BTC ≥3/day") shown
// as chips in the STRAT tab's Browse Traders panel. Applying one just writes
// into the shared FiltersContext — the leaderboard itself already auto-refreshes
// hourly via FiltersContext's reloadKey interval, so a "pinned" leaderboard
// needs no polling logic of its own.

export interface LeaderboardPreset {
  id: string;
  name: string;
  category: string;
  marketQuery: string;
  daysAgo: string;
  minPerDay: string;
}

const STORAGE_KEY = "poly8bit_leaderboard_presets";

export function loadPresets(): LeaderboardPreset[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function savePresets(list: LeaderboardPreset[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {}
}
