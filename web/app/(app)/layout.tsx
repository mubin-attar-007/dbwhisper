import type { Metadata } from "next";
import { AuthBar } from "../components/AuthBar";
import { AuthProvider } from "../components/AuthProvider";
import { Footer } from "../components/Footer";

export const metadata: Metadata = {
  title: "Console",
};

// Auth UI appears only when NEXT_PUBLIC_AUTH_ENABLED=true (pair with backend USER_AUTH_ENABLED).
const authEnabled = process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";

export default function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const content = (
    <>
      <div className="flex-1">{children}</div>
      <Footer />
    </>
  );
  return authEnabled ? (
    <AuthProvider>
      <AuthBar />
      {content}
    </AuthProvider>
  ) : (
    content
  );
}
