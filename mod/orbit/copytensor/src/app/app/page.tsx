import { redirect } from "next/navigation";

// basePath ("/copytensor") is prepended automatically — pass the path WITHOUT it.
export default function Home() {
  redirect("/subnets");
}
