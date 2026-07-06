"use client";

import TopBar from "../components/TopBar";
import MarketsGrid from "../components/MarketsGrid";
import { useFilters } from "../context/FiltersContext";

export default function MarketsPage() {
  const { search, sort, setSort, category, reloadKey, reload } = useFilters();

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="SEARCH MARKETS..." />
      <div className="p-4 space-y-4">
        <MarketsGrid
          search={search}
          sort={sort}
          setSort={setSort}
          category={category}
          daysAgo=""
          reloadKey={reloadKey}
          reload={reload}
        />
      </div>
    </div>
  );
}
