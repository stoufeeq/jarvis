import { redirect } from "next/navigation";

// Root redirects to dashboard
export default function Home() {
  redirect("/dashboard");
}
