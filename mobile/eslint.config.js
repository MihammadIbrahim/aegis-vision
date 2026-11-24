// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require("eslint/config");
const expoConfig = require("eslint-config-expo/flat");

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ["dist/*", "node_modules/*"],
  },
  {
    // Jest mock factories must use require() (they run before imports).
    files: ["src/test/**", "jest.setup.ts", "jest.after-env.ts"],
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
]);
