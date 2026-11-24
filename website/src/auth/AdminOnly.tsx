import type { ReactNode } from "react";

import { useAuth } from "./AuthContext";
import { canManagePersonnel } from "./permissions";

/** Pages that only Organization Admins may open. The API refuses everyone else as well. */
export function AdminOnly({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  if (!canManagePersonnel(session?.role)) {
    return (
      <div className="notice error" role="alert">
        Only an Organization Admin can use this page.
      </div>
    );
  }
  return <>{children}</>;
}
