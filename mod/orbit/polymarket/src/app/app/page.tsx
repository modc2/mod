import { redirect } from "next/navigation";

// The console IS the COPY DESK: copy individual traders, with an amount
// against each name. There is no second product behind it — the multi-trader
// strat hub is archived (src/_archive/README.md) and /strats forwards here.
//
// basePath ("/polymarket") is prepended automatically — pass the path WITHOUT it.
export default function Home() {
  redirect("/copy");
}
