import { redirect } from "next/navigation";

/** There is no "home". Signed in you go to Global; the middleware handles the rest. */
export default function RootPage() {
  redirect("/global");
}
