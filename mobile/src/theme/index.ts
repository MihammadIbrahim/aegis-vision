import { colors } from "./colors";

export { colors };

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32 } as const;
export const radius = { sm: 8, md: 12, lg: 16, xl: 20, pill: 999 } as const;

export const typography = {
  title: { fontSize: 24, fontWeight: "700" as const, color: colors.text },
  heading: { fontSize: 18, fontWeight: "700" as const, color: colors.text },
  subheading: { fontSize: 15, fontWeight: "600" as const, color: colors.text },
  body: { fontSize: 15, color: colors.text },
  small: { fontSize: 13, color: colors.textMuted },
  label: { fontSize: 13, fontWeight: "600" as const, color: colors.textMuted },
  caption: { fontSize: 11, fontWeight: "600" as const, color: colors.textSubtle, letterSpacing: 0.6 },
} as const;

/** Minimum touch target (accessibility). */
export const TOUCH_TARGET = 48;
