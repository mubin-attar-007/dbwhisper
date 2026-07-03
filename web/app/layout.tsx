import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { SITE_URL } from "@/src/lib/site";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
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
    url: "/",
    siteName: "DBWhisper",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "DBWhisper — Natural Language to SQL",
    description:
      "Ask questions in plain English and get SQL plus results from the DBWhisper API.",
  },
};

export const viewport: Viewport = {
  themeColor: "#0f172a",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

// Chrome (auth, footer) now lives in the route-group layouts: (app) for the console,
// (marketing) for the landing page. The root only owns <html>/<body>, fonts, and metadata.
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`dark ${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
        {children}
      </body>
    </html>
  );
}
