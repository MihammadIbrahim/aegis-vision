import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

import { useSession } from "../auth/SessionProvider";
import {
  ensureAlarmChannel,
  forgetPushToken,
  installNotificationHandler,
  type PushRegistration,
  registerPushToken,
} from "./notifications";

export type PushStatus = { status: "checking" } | PushRegistration;

const PushContext = createContext<PushStatus>({ status: "checking" });

/** Signed in: create the alarm channel, register this phone's push token; at sign-out: forget it. */
export function PushStatusProvider({
  children,
  register = registerPushToken,
  forget = forgetPushToken,
}: {
  children: ReactNode;
  register?: () => Promise<PushRegistration>;
  forget?: () => Promise<void>;
}) {
  const { onSignOut } = useSession();
  const [status, setStatus] = useState<PushStatus>({ status: "checking" });

  useEffect(() => {
    let cancelled = false;
    installNotificationHandler();
    void (async () => {
      await ensureAlarmChannel().catch(() => undefined);
      const result = await register().catch(
        (): PushRegistration => ({ status: "unavailable", reason: "Push registration failed." }),
      );
      if (!cancelled) setStatus(result);
    })();
    return () => {
      cancelled = true;
    };
  }, [register]);

  useEffect(() => onSignOut(() => forget().catch(() => undefined)), [onSignOut, forget]);

  return <PushContext.Provider value={status}>{children}</PushContext.Provider>;
}

export function usePushStatus(): PushStatus {
  return useContext(PushContext);
}
