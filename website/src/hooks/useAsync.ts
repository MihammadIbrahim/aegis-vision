import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/** Runs ``load`` when ``deps`` change (and on ``reload``). Results of an older run that finishes
 * after a newer one, or after unmount, are ignored. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const run = useRef(0);
  const stable = useCallback(load, deps);

  useEffect(() => {
    const id = ++run.current;
    setLoading(true);
    setError(null);
    stable()
      .then((value) => {
        if (id === run.current) setData(value);
      })
      .catch((e: unknown) => {
        if (id === run.current) setError(e);
      })
      .finally(() => {
        if (id === run.current) setLoading(false);
      });
    return () => {
      run.current++;
    };
  }, [stable, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, loading, reload };
}
