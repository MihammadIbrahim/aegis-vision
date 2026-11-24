import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import type { ReactNode } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, radius } from "../theme";

/** The navy header used on every screen. */
export function Header({
  title,
  subtitle,
  back = false,
  right,
  children,
}: {
  title: string;
  subtitle?: string;
  back?: boolean;
  right?: ReactNode;
  children?: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  return (
    <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
      <View style={styles.row}>
        {back ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Back"
            onPress={() => (router.canGoBack() ? router.back() : router.replace("/alerts"))}
            style={styles.back}
            hitSlop={8}
          >
            <Ionicons name="arrow-back" size={22} color={colors.textOnNavy} />
          </Pressable>
        ) : null}
        <View style={styles.titles}>
          <Text style={styles.title} accessibilityRole="header" numberOfLines={2}>
            {title}
          </Text>
          {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
        </View>
        {right}
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  header: { backgroundColor: colors.navy, paddingHorizontal: 20, paddingBottom: 18, gap: 12 },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  back: {
    width: 44,
    height: 44,
    borderRadius: radius.md,
    backgroundColor: colors.navyRaised,
    alignItems: "center",
    justifyContent: "center",
  },
  titles: { flex: 1, gap: 2 },
  title: { color: colors.textOnNavy, fontSize: 22, fontWeight: "700" },
  subtitle: { color: colors.textOnNavyMuted, fontSize: 13 },
});
