import { redirect } from "next/navigation";

// The board used to have a tab of its own. It rendered the same ranking
// TRADERS does, so the tab is gone and the old route just forwards —
// bookmarks and shared links into /leaderboard still land somewhere real.
export default function LeaderboardPage() {
  redirect("/traders");
}
