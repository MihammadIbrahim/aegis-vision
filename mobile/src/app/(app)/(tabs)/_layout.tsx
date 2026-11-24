import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";
import type { ComponentProps } from "react";
import type { ColorValue } from "react-native";

import { colors } from "../../../theme";

type Icon = ComponentProps<typeof Ionicons>["name"];

function icon(name: Icon) {
  return function TabIcon({ color, size }: { color: ColorValue; size: number }) {
    return <Ionicons name={name} color={typeof color === "number" || color === null ? undefined : color} size={size} />;
  };
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textSubtle,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border, height: 64, paddingBottom: 8 },
        tabBarLabelStyle: { fontSize: 11, fontWeight: "700" },
      }}
    >
      <Tabs.Screen name="alerts" options={{ title: "Alerts", tabBarIcon: icon("notifications") }} />
      <Tabs.Screen name="chats" options={{ title: "Chats", tabBarIcon: icon("chatbubbles") }} />
      <Tabs.Screen name="report" options={{ title: "Manual Report", tabBarIcon: icon("add-circle") }} />
      <Tabs.Screen name="my-reports" options={{ title: "My Reports", tabBarIcon: icon("document-text") }} />
      <Tabs.Screen name="profile" options={{ title: "Profile", tabBarIcon: icon("person") }} />
    </Tabs>
  );
}
