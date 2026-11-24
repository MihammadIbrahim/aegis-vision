/** Client-side checks that mirror the server's rules. The server always has the final say. */

const MODERN_GUARD_ID = /^[A-Z]{6}[0-9]{21}$/; // 6 capital letters + 21 digits (decision T)
const LEGACY_GUARD_ID = /^GRD-[0-9]{11}$/; // still valid for older accounts

export function normalizeGuardId(value: string): string {
  return value.trim().toUpperCase();
}

export function isGuardId(value: string): boolean {
  const id = normalizeGuardId(value);
  return MODERN_GUARD_ID.test(id) || LEGACY_GUARD_ID.test(id);
}

export function isEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value.trim());
}

export function isOtp(value: string): boolean {
  return /^[0-9]{6}$/.test(value);
}

export interface PasswordCheck {
  label: string;
  ok: boolean;
}

/** The server's password policy: 12–128 characters, a letter and a digit, not containing the username or email. */
export function passwordChecks(password: string, confirmation: string, avoid: string[] = []): PasswordCheck[] {
  const lower = password.toLowerCase();
  const checks: PasswordCheck[] = [
    { label: "12 to 128 characters", ok: password.length >= 12 && password.length <= 128 },
    { label: "At least one letter", ok: /[A-Za-z]/.test(password) },
    { label: "At least one digit", ok: /[0-9]/.test(password) },
    { label: "Both entries match", ok: password.length > 0 && password === confirmation },
  ];
  const named = avoid.map((v) => v.trim().toLowerCase()).filter((v) => v.length >= 3);
  if (named.length) {
    checks.push({ label: "Does not contain your username or email", ok: !named.some((v) => lower.includes(v)) });
  }
  return checks;
}

export function passwordAcceptable(password: string, confirmation: string, avoid: string[] = []): boolean {
  return passwordChecks(password, confirmation, avoid).every((c) => c.ok);
}

/** Phone numbers: 7–15 digits, an optional leading +; spaces, dots, dashes and brackets are ignored. */
export function isPhone(value: string): boolean {
  const cleaned = value.replace(/[\s.()-]/g, "");
  return /^\+?[0-9]{7,15}$/.test(cleaned);
}
