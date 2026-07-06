import RootClient from "./RootClient";

async function fetchRoot() {
  try {
    const res = await fetch(
      `${process.env.WHITEPAPER_API_URL || "http://localhost:50106"}/tree/root`,
      { cache: "no-store" }
    );
    if (!res.ok) return null;
    return (await res.json()) as { root: string | null; epoch: number | null; count?: number };
  } catch {
    return null;
  }
}

export default async function Page() {
  const root = await fetchRoot();
  return <RootClient initialRoot={root} />;
}
