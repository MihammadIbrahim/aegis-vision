import { Redirect, Stack } from "expo-router";
import { View } from "react-native";

import { AlarmOverlay } from "../../alarm/AlarmOverlay";
import { AlarmProvider } from "../../alarm/AlarmProvider";
import { useSession } from "../../auth/SessionProvider";
import { CallProvider } from "../../calls/CallProvider";
import { ActiveCallOverlay, IncomingCallCard } from "../../calls/CallViews";
import { Loading } from "../../components/States";
import { PushStatusProvider } from "../../notifications/PushStatus";
import { OutboxProvider } from "../../offline/OutboxProvider";

/** Everything behind sign-in. Overlays stack: screens < incoming call / active call < alarm (a native modal). */
export default function AppLayout() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading label="Starting…" />;
  if (state.status !== "signedIn") return <Redirect href="/sign-in" />;
  return (
    <PushStatusProvider>
      <OutboxProvider>
        <AlarmProvider>
          <CallProvider>
            <View style={{ flex: 1 }}>
              <Stack screenOptions={{ headerShown: false }} />
              <ActiveCallOverlay />
              <IncomingCallCard />
              <AlarmOverlay />
            </View>
          </CallProvider>
        </AlarmProvider>
      </OutboxProvider>
    </PushStatusProvider>
  );
}
