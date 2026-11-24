// Jest 30 ships the `jest` object's types in @jest/environment; @types/jest 30 declares the other globals.
import type { Jest } from "@jest/environment";

declare global {
  const jest: Jest;
}

export {};
