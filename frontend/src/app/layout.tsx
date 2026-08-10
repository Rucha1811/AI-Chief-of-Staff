import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import AuthGate from "@/components/AuthGate";
import NotificationBell from "@/components/NotificationBell";
import RegisterSW from "@/components/RegisterSW";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AI Chief of Staff",
  description: "Your personal AI assistant that manages emails, calendar, documents, tasks, and daily planning.",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "AI Chief",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={inter.className}>
        <RegisterSW />
        <AuthGate>
          <div className="fixed top-4 right-4 z-50">
            <NotificationBell />
          </div>
          <Sidebar />
          <main className="md:ml-[76px] lg:ml-64 min-h-screen p-4 pt-20 md:p-6 lg:p-8 pb-16 md:pb-8">
            {children}
          </main>
        </AuthGate>
      </body>
    </html>
  );
}
