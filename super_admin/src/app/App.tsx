import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { LoginPage } from "../auth/LoginPage";
import { LoadingState } from "../components/ui";
import { AiSettingsPage } from "../features/ai-settings/AiSettingsPage";
import { AuditLogsPage } from "../features/audit-logs/AuditLogsPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { HealthPage } from "../features/health/HealthPage";
import { OrganizationDetailPage } from "../features/organizations/OrganizationDetailPage";
import { OrganizationsPage } from "../features/organizations/OrganizationsPage";
import { ProfilePage } from "../features/profile/ProfilePage";
import { AppShell } from "./layouts/AppShell";
import { AuthLayout } from "./layouts/AuthLayout";

export function App() {
  const { session, loading } = useAuth();
  if (loading) return <LoadingState />;
  if (!session) {
    return (
      <AuthLayout>
        <Routes>
          <Route path="*" element={<LoginPage />} />
        </Routes>
      </AuthLayout>
    );
  }
  return (
    <AppShell>
      <Routes>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/organizations" element={<OrganizationsPage />} />
        <Route path="/organizations/:organizationId" element={<OrganizationDetailPage />} />
        <Route path="/ai-settings" element={<AiSettingsPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="/audit-logs" element={<AuditLogsPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppShell>
  );
}
