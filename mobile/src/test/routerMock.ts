/* A stand-in for expo-router in screen tests: records navigation, serves route params. */
export const mockRouter = {
  push: jest.fn(),
  replace: jest.fn(),
  back: jest.fn(),
  canGoBack: jest.fn(() => true),
};

export const mockParams: { current: Record<string, string> } = { current: {} };

export const routerModule = {
  useRouter: () => mockRouter,
  useLocalSearchParams: () => mockParams.current,
  Redirect: () => null,
  Stack: () => null,
  Tabs: Object.assign(() => null, { Screen: () => null }),
};

export function resetRouter(): void {
  Object.values(mockRouter).forEach((fn) => (fn as jest.Mock).mockClear());
  mockParams.current = {};
}
