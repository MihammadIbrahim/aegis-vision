import { Redirect, Stack } from "expo-router";

import { useSession } from "../../auth/SessionProvider";
import { Loading } from "../../components/States";

/** Sign-in, code, forgot and reset password: only while signed out. No registration screen exists. */
export default function AuthLayout() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading label="Starting…" />;
  if (state.status === "signedIn") return <Redirect href="/alerts" />;
  return <Stack screenOptions={{ headerShown: false }} />;
}
