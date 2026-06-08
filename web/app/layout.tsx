import type { Metadata } from "next";
import "./globals.css";
import { AuthBar } from "./components/AuthBar";
import { AuthProvider } from "./components/AuthProvider";

export const metadata: Metadata = {
  title: "DBWhisper — NL → SQL Console",
  description:
    "Ask questions in plain English and get SQL plus results from the DBWhisper API.",
};

// Opt-in: the auth UI appears only when NEXT_PUBLIC_AUTH_ENABLED=true (pair it with the backend
// USER_AUTH_ENABLED). Unset → the demo console renders exactly as before.
const authEnabled = process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-slate-950 text-slate-100">
        {authEnabled ? (
          <AuthProvider>
            <AuthBar />
            {children}
          </AuthProvider>
        ) : (
          children
        )}
      </body>
    </html>
  );
}
