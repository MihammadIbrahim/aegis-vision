import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { canViewPersonnel } from "../auth/permissions";
import { ForgotPasswordPage } from "../auth/ForgotPasswordPage";
import { LoginPage } from "../auth/LoginPage";
import { LandingPage } from "../features/landing/LandingPage";
import { CampusRequestPage, RegisterPage } from "../features/registration";
import { AppShell } from "./layouts/AppShell";
import { AuthLayout } from "./layouts/AuthLayout";
import { APP_ROUTES } from "./routes";

export function App() {
  const { session, loading } = useAuth();
  if (loading) return <p className="content">Loading…</p>;
  if (!session || !canViewPersonnel(session.role)) {
    // Signed out: the public landing page at "/", sign-in, forgot password, registration and campus requests
    // ("Already Registered Organization?"); any other address shows
    // the sign-in page (a deep link opens after signing in).
    return (
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route
          path="/register"
          element={
            <AuthLayout width="xl">
              <RegisterPage />
            </AuthLayout>
          }
        />
        <Route
          path="/register/campus"
          element={
            <AuthLayout width="xl">
              <CampusRequestPage />
            </AuthLayout>
          }
        />
        <Route
          path="/forgot-password"
          element={
            <AuthLayout>
              <ForgotPasswordPage />
            </AuthLayout>
          }
        />
        <Route
          path="*"
          element={
            <AuthLayout>
              <LoginPage />
            </AuthLayout>
          }
        />
      </Routes>
    );
  }
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        {APP_ROUTES.map((r) => (
          <Route key={r.path} path={r.path} element={r.element} />
        ))}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppShell>
  );
}
