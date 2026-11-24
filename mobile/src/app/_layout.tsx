import { QueryClientProvider } from "@tanstack/react-query";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthFlowProvider } from "../auth/AuthFlowProvider";
import { SessionProvider } from "../auth/SessionProvider";
import { NetworkProvider } from "../lib/network";
import { createQueryClient } from "../lib/queryClient";
import { RealtimeProvider } from "../realtime/RealtimeProvider";

export default function RootLayout() {
  const [queryClient] = useState(createQueryClient);
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <NetworkProvider>
          <SessionProvider>
            <AuthFlowProvider>
              <RealtimeProvider>
                <StatusBar style="light" />
                <Stack screenOptions={{ headerShown: false }} />
              </RealtimeProvider>
            </AuthFlowProvider>
          </SessionProvider>
        </NetworkProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
