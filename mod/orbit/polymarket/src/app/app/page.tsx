import { redirect } from "next/navigation";

// basePath ("/polymarket") is prepended automatically — pass the path WITHOUT it.
export default function Home() {
  redirect("/markets");
}
