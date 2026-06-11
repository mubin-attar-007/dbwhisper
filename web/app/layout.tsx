import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthBar } from "./components/AuthBar";
import { AuthProvider } from "./components/AuthProvider";
import { Footer } from "./components/Footer";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000",
  ),
  title: {
    default: "DBWhisper — Natural Language to SQL",
    template: "%s · DBWhisper",
  },
  description:
    "Ask questions in plain English and get SQL plus results from the DBWhisper API.",
  openGraph: {
    title: "DBWhisper — Natural Language to SQL",
    description:
      "Ask questions in plain English and get SQL plus results from the DBWhisper API.",
    siteName: "DBWhisper",
    type: "website",
  },
  twitter: {
    card: "summary",
  },
};

export const viewport: Viewport = {
  themeColor: "#0f172a",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

// Opt-in: the auth UI appears only when NEXT_PUBLIC_AUTH_ENABLED=true (pair it with the backend
// USER_AUTH_ENABLED). Unset → the demo console renders exactly as before.
const authEnabled = process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`dark ${inter.variable}`}>
      <body className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
        {authEnabled ? (
          <AuthProvider>
            <AuthBar />
            <div className="flex-1">{children}</div>
          </AuthProvider>
        ) : (
          <div className="flex-1">{children}</div>
        )}
        <Footer />
      </body>
    </html>
  );
}
