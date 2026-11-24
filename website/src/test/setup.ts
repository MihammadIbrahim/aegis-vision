import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { setRealtimeSocketFactory } from "../realtime/socket";
import { FakeSocket } from "./realtimeFakes";

// Pages under test never open a real WebSocket: every socket is a fake the test can drive (decision D-026).
setRealtimeSocketFactory((url) => new FakeSocket(url));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
  FakeSocket.instances = [];
});
