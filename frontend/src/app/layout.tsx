import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Claude Office — 司令塔オフィス可視化",
  description:
    "Claude Code と Commander Bridge の稼働状況を仮想オフィスでリアルタイムに可視化するピクセルアート画面。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Default to Japanese to match えむ's primary install. The runtime locale
  // for i18n copy is managed separately via preferencesStore; this attribute
  // only drives the browser's own language hint (spell-check, hyphenation,
  // accessibility tree, etc.).
  return (
    <html lang="ja">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
