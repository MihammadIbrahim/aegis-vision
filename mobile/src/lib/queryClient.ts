import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";

/** Network problems are retried twice; answers from the server (4xx) are not. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 2_000,
        retry: (count, error) => error instanceof ApiError && (error.isNetworkError || error.status >= 500) && count < 2,
      },
      mutations: { retry: false },
    },
  });
}
