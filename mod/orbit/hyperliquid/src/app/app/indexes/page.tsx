import { redirect } from "next/navigation";
// Indexes were renamed to Strats — keep old links working.
export default function IndexesRedirect() {
  redirect("/strats");
}
