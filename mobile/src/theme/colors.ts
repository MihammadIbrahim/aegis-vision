/**
 * Colour palette of the Guard app.
 *
 * Every value was sampled from the project owner's reference phone designs (colour scheme only; the screens, fields
 * and flows come from the FYP documentation and the Core API contract). Names describe the role, not the hue.
 */
export const colors = {
  // Brand and structure
  navy: "#101D35", // headers, the sign-in hero and the active-call overlay
  navyRaised: "#17253E", // raised elements on navy
  navyBorder: "#243452",
  primary: "#2855E8", // primary buttons, links, own chat bubbles, the active tab
  primaryPressed: "#2147C4",
  primaryTint: "#EBF0FF", // icon tiles, selected tab background, "Retrying" badge
  primaryBorder: "#C9D4F9",
  onPrimary: "#FFFFFF",

  // Surfaces
  background: "#F3F5F9", // screen background
  surface: "#FFFFFF", // cards, sheets
  inputBackground: "#F8FAFD",
  border: "#E4E9F1",
  borderStrong: "#DCE3ED",

  // Text
  text: "#14233D",
  textMuted: "#586981",
  textSubtle: "#64748B",
  textOnNavy: "#FFFFFF",
  textOnNavyMuted: "#C9D4F9",

  // Severity and status
  danger: "#C33643", // high priority, errors, sign out
  dangerStrong: "#C62F45",
  dangerTint: "#FDECEE",
  dangerBorder: "#EFBFC3",
  warning: "#83530B", // standard priority, "Acknowledged", offline banner, "Pending offline"
  warningTint: "#FFF4DC",
  warningBorder: "#EFE0C2",
  success: "#15734E", // "Responding", "Under control", "Synced", online
  successTint: "#E6F5EE",
  successOnNavy: "#7DE4B0", // the "Online" pill on navy
  successPillOnNavy: "#173E3C",
  info: "#1C3667", // Supervisor / Admin directives in chat
  infoTint: "#EEF2FE",

  overlay: "rgba(16, 29, 53, 0.72)",
  transparent: "transparent",
} as const;

export type ColorName = keyof typeof colors;
