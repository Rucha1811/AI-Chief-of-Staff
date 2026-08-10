"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { clsx } from "clsx";
import {
  LayoutDashboard,
  Mail,
  Calendar,
  CalendarClock,
  CheckSquare,
  FileText,
  Newspaper,
  Sparkles,
  GraduationCap,
  ClipboardList,
  UserCheck,
  Menu,
  X,
} from "lucide-react";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/briefing", label: "Daily Briefing", icon: Sparkles },
  { href: "/news", label: "News", icon: Newspaper },
  { href: "/emails", label: "Emails", icon: Mail },
  { href: "/calendar", label: "Calendar", icon: Calendar },
  { href: "/timetable", label: "Timetable", icon: CalendarClock },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/exams", label: "Exams", icon: GraduationCap },
  { href: "/assignments", label: "Assignments", icon: ClipboardList },
  { href: "/attendance", label: "Attendance", icon: UserCheck },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  const renderNav = (compact: boolean) => (
    <nav className="flex-1 px-2 md:px-3 py-4 space-y-1 overflow-y-auto scrollbar-thin">
      {navItems.map(({ href, label, icon: Icon }) => {
        const isActive = pathname === href;
        return (
          <Link
            key={href}
            href={href}
            title={label}
            className={clsx(
              "flex items-center gap-3 rounded-xl text-sm font-medium transition-all duration-150",
              compact ? "justify-center px-0 py-3 lg:justify-start lg:px-3 lg:py-2.5" : "px-3 py-2.5",
              isActive
                ? "text-white bg-instagram shadow-md shadow-pink-600/25"
                : "text-slate-500 hover:text-slate-800 hover:bg-slate-100"
            )}
          >
            <Icon size={18} strokeWidth={isActive ? 2.4 : 2} />
            <span className={compact ? "hidden lg:inline" : undefined}>{label}</span>
          </Link>
        );
      })}
    </nav>
  );

  return (
    <>
      {/* Mobile top bar */}
      <header className="md:hidden fixed top-0 left-0 right-0 h-14 bg-white/90 backdrop-blur-xl border-b border-slate-200 z-40 flex items-center gap-3 px-4">
        <button
          onClick={() => setOpen(true)}
          className="p-1.5 -ml-1.5 text-slate-600 hover:text-slate-900"
          aria-label="Open menu"
        >
          <Menu className="w-6 h-6" />
        </button>
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-instagram flex items-center justify-center shadow shadow-pink-600/30">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <span className="text-sm font-bold text-slate-900">AI Chief</span>
        </div>
      </header>

      {/* Mobile drawer backdrop */}
      {open && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Mobile drawer */}
      <aside
        className={clsx(
          "md:hidden fixed left-0 top-0 h-screen w-64 bg-white flex flex-col z-50 transition-transform duration-200",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex items-center justify-between px-5 py-5 border-b border-slate-200">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-instagram flex items-center justify-center shadow-lg shadow-pink-600/30">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div>
              <h1 className="text-base font-bold text-slate-900 leading-tight">AI Chief</h1>
              <p className="text-xs text-slate-500 font-medium">of Staff</p>
            </div>
          </div>
          <button onClick={() => setOpen(false)} className="p-1.5 text-slate-500 hover:text-slate-900" aria-label="Close menu">
            <X className="w-5 h-5" />
          </button>
        </div>
        {renderNav(false)}
        <div className="px-4 py-4 border-t border-slate-200">
          <p className="text-xs text-slate-400 font-medium">v1.0.0 &middot; AI Chief of Staff</p>
        </div>
      </aside>

      {/* Desktop sidebar: icon-only on md, full labels on lg+ */}
      <aside className="hidden md:flex fixed left-0 top-0 h-screen w-[76px] lg:w-64 bg-white/85 border-r border-slate-200 backdrop-blur-xl flex-col z-30 before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-pink-600/70 before:to-transparent">
        <div className="flex items-center gap-3 px-0 lg:px-6 py-5 justify-center lg:justify-start border-b border-slate-200">
          <div className="w-10 h-10 rounded-xl bg-instagram flex items-center justify-center shadow-lg shadow-pink-600/30 shrink-0">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div className="hidden lg:block">
            <h1 className="text-base font-bold text-slate-900 leading-tight">AI Chief</h1>
            <p className="text-xs text-slate-500 font-medium">of Staff</p>
          </div>
        </div>
        {renderNav(true)}
        <div className="px-0 lg:px-4 py-4 border-t border-slate-200 text-center lg:text-left">
          <p className="text-xs text-slate-400 font-medium hidden lg:block">v1.0.0 &middot; AI Chief of Staff</p>
        </div>
      </aside>
    </>
  );
}
