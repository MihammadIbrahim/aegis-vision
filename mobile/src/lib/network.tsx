import NetInfo, { type NetInfoState } from "@react-native-community/netinfo";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

/** Online = a network is connected and the internet is not known to be unreachable. */
export function isOnline(state: Pick<NetInfoState, "isConnected" | "isInternetReachable">): boolean {
  return state.isConnected === true && state.isInternetReachable !== false;
}

const NetworkContext = createContext<boolean>(true);

export function NetworkProvider({ children }: { children: ReactNode }) {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener((state) => setOnline(isOnline(state)));
    void NetInfo.fetch().then((state) => setOnline(isOnline(state)));
    return unsubscribe;
  }, []);
  return <NetworkContext.Provider value={online}>{children}</NetworkContext.Provider>;
}

export function useOnline(): boolean {
  return useContext(NetworkContext);
}
