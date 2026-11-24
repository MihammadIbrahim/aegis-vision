import { Redirect } from "expo-router";

import { useSession } from "../auth/SessionProvider";
import { Loading } from "../components/States";

export default function Index() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading label="Starting…" />;
  return <Redirect href={state.status === "signedIn" ? "/alerts" : "/sign-in"} />;
}
