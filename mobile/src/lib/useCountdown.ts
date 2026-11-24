import { useCallback, useEffect, useState } from "react";

/** Seconds left until a resend is allowed; restart(n) after each send. */
export function useCountdown(initialSeconds: number) {
  const [seconds, setSeconds] = useState(Math.max(0, initialSeconds));
  useEffect(() => {
    if (seconds <= 0) return;
    const timer = setTimeout(() => setSeconds((s) => Math.max(0, s - 1)), 1000);
    return () => clearTimeout(timer);
  }, [seconds]);
  const restart = useCallback((value: number) => setSeconds(Math.max(0, value)), []);
  return { seconds, restart };
}
