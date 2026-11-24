import * as SecureStore from "expo-secure-store";

const OPTIONS: SecureStore.SecureStoreOptions = { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY };

export async function secureGet<T>(key: string): Promise<T | null> {
  const raw = await SecureStore.getItemAsync(key, OPTIONS);
  if (raw === null) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export async function secureSet(key: string, value: unknown): Promise<void> {
  await SecureStore.setItemAsync(key, JSON.stringify(value), OPTIONS);
}

export async function secureDelete(key: string): Promise<void> {
  await SecureStore.deleteItemAsync(key, OPTIONS);
}
