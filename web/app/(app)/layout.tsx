import type { Metadata } from "next";
import { AppSidebar } from "../components/AppSidebar";
import { AppTopBar } from "../components/AppTopBar";
import { AuthProvider } from "../components/AuthProvider";
import { WorkspaceProvider } from "../components/WorkspaceProvider";

export const metadata: Metadata = {
  title: "Console",
};

// Auth UI appears only when NEXT_PUBLIC_AUTH_ENABLED=true (pair with backend USER_AUTH_ENABLED).
const authEnabled = process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";

export default function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const shell = (
    <WorkspaceProvider>
      <div className="flex h-screen flex-col">
        <AppTopBar />
        <div className="flex min-h-0 flex-1">
          <AppSidebar />
          <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
    </WorkspaceProvider>
  );
  return authEnabled ? <AuthProvider>{shell}</AuthProvider> : shell;
}
