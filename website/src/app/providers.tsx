import type { ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";

import { AuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>{children}</ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
