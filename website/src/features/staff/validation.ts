// The same rules as the server (registration_policy.py); the server checks again.

export const PASSWORD_HINT = "12–128 characters with at least one letter and one digit; not the username or email.";

const USERNAME = /^[A-Za-z0-9_.-]{3,64}$/;
const PHONE = /^\+?[0-9]{7,15}$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function fullNameProblem(value: string): string | undefined {
  const name = value.trim();
  if (!name) return "Enter the full name.";
  if (name.length < 2 || name.length > 120) return "The full name must be 2 to 120 characters long.";
  if (!/\p{L}/u.test(name)) return "The full name must contain letters.";
  return undefined;
}

export function usernameProblem(value: string): string | undefined {
  return USERNAME.test(value.trim()) ? undefined : "The username must be 3 to 64 letters, digits, '.', '_' or '-'.";
}

export function phoneProblem(value: string): string | undefined {
  return PHONE.test(value.replace(/[\s().-]/g, "")) ? undefined : "Enter a phone number of 7 to 15 digits, optionally starting with +.";
}

export function emailProblem(value: string): string | undefined {
  return EMAIL.test(value.trim()) ? undefined : "Enter a valid email address.";
}

export function passwordProblem(password: string, username: string, email: string): string | undefined {
  if (password.length < 12 || password.length > 128) return "The password must be 12 to 128 characters long.";
  if (!/\p{L}/u.test(password) || !/[0-9]/.test(password)) return "The password must contain at least one letter and one digit.";
  if (username.trim() && password.toLowerCase().includes(username.trim().toLowerCase())) {
    return "The password must not contain the username.";
  }
  if (email.trim() && password.toLowerCase() === email.trim().toLowerCase()) return "The password must not be the email address.";
  return undefined;
}

type FormFields = { full_name: string; username: string; email: string; phone_number: string; password: string };

/** Problems per field of the creation form (only fields with a problem appear). */
export function formProblems(f: FormFields): Partial<Record<keyof FormFields, string>> {
  const found: Partial<Record<keyof FormFields, string>> = {
    full_name: fullNameProblem(f.full_name),
    username: usernameProblem(f.username),
    email: emailProblem(f.email),
    phone_number: phoneProblem(f.phone_number),
    password: passwordProblem(f.password, f.username, f.email),
  };
  return Object.fromEntries(Object.entries(found).filter(([, v]) => v)) as Partial<Record<keyof FormFields, string>>;
}
