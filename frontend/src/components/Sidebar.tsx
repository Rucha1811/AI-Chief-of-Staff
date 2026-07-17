"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import {
  LayoutDashboard,
  Mail,
  Calendar,
  CheckSquare,
  FileText,
  Newspaper,
  Bot,
} from "lucide-react";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/briefing", label: "Daily Briefing", icon: Newspaper },
  { href: "/emails", label: "Emails", icon: Mail },
  { href: "/calendar", label: "Calendar", icon: Calendar },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/documents", label: "Documents", icon: FileText },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 h-screen w-64 bg-slate-900/80 border-r border-slate-800 backdrop-blur-xl flex flex-col z-30">
      <div className="flex items-center gap-3 px-6 py-5 border-b border-slate-800">
        <div className="w-9 h-9 rounded-lg bg-indigo-600 flex items-center justify-center">
          <Bot className="w-5 h-5 text-white" />
        </div>
        <div>
          <h1 className="text-base font-semibold text-slate-100">AI Chief</h1>
          <p className="text-xs text-slate-500">of Staff</p>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto scrollbar-thin">
        {navItems.map(({ href, label, icon: Icon }) => {
          const isActive = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                isActive
                  ? "bg-indigo-600/20 text-indigo-400"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
              )}
            >
              <Icon className="w-4.5 h-4.5" size={18} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="px-4 py-4 border-t border-slate-800">
        <p className="text-xs text-slate-600">
          v1.0.0 &middot; AI Chief of Staff
        </p>
      </div>
    </aside>
  );
}
