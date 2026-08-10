"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Bell, CheckCheck, BellRing } from "lucide-react";

interface Notif {
  id: number;
  type: string;
  title: string;
  message: string;
  entity_type: string | null;
  entity_id: number | null;
  priority: string;
  is_read: boolean;
  created_at: string | null;
}

const PRIORITY_COLOR: Record<string, string> = {
  high: "bg-red-500",
  normal: "bg-amber-400",
  low: "bg-slate-300",
};

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}

async function ensurePushSubscription(): Promise<boolean> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
  if (!("Notification" in window) || Notification.permission !== "granted") return false;
  try {
    const reg = await navigator.serviceWorker.ready;
    const existing = await reg.pushManager.getSubscription();
    if (existing) return true;
    const { key } = await api.notifications.vapidPublicKey();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key) as unknown as BufferSource,
    });
    const json = sub.toJSON();
    await api.notifications.subscribe({
      endpoint: json.endpoint!,
      keys: { p256dh: json.keys!.p256dh, auth: json.keys!.auth },
    });
    return true;
  } catch {
    return false;
  }
}

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notif[]>([]);
  const [unread, setUnread] = useState(0);
  const [pushOn, setPushOn] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const prevUnread = useRef(0);
  const panelRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, count] = await Promise.all([
        api.notifications.list(30),
        api.notifications.unreadCount(),
      ]);
      setItems(list);
      setUnread(count.count);
      if (
        count.count > prevUnread.current &&
        typeof window !== "undefined" &&
        "Notification" in window &&
        Notification.permission === "granted" &&
        document.visibilityState === "hidden"
      ) {
        new Notification("AI Chief", {
          body: `${count.count - prevUnread.current} new reminder${count.count - prevUnread.current > 1 ? "s" : ""}`,
        });
      }
      prevUnread.current = count.count;
    } catch {
      /* backend unreachable — pages surface their own errors */
    }
  }, []);

  const syncPushState = useCallback(() => {
    if (!("Notification" in window)) {
      setPushOn(false);
      return;
    }
    if (Notification.permission === "granted") {
      if ("serviceWorker" in navigator) {
        navigator.serviceWorker.ready
          .then((reg) => reg.pushManager.getSubscription())
          .then((sub) => setPushOn(!!sub))
          .catch(() => setPushOn(false));
      } else {
        setPushOn(false);
      }
    } else {
      setPushOn(false);
    }
  }, []);

  useEffect(() => {
    syncPushState();
    window.addEventListener("aichief:auth", syncPushState);
    return () => window.removeEventListener("aichief:auth", syncPushState);
  }, [syncPushState]);

  const togglePush = async () => {
    if (pushOn) {
      setPushBusy(true);
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) await sub.unsubscribe();
      } catch {
        /* ignore */
      } finally {
        setPushOn(false);
        setPushBusy(false);
      }
      return;
    }
    setPushBusy(true);
    try {
      if (!("Notification" in window)) return;
      if (Notification.permission === "default") {
        const granted = await Notification.requestPermission();
        if (granted !== "granted") return;
      }
      if (Notification.permission !== "granted") return;
      const ok = await ensurePushSubscription();
      setPushOn(ok);
    } finally {
      setPushBusy(false);
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 60_000);
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") refresh();
    });
    const onClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [refresh]);

  const markRead = async (id: number) => {
    setItems((prev) => prev.map((n) => (n.id === id ? { ...n, is_read: true } : n)));
    setUnread((u) => Math.max(0, u - 1));
    try {
      await api.notifications.markRead(id);
    } catch {
      /* ignore */
    }
  };

  const readAll = async () => {
    setItems((prev) => prev.map((n) => ({ ...n, is_read: true })));
    setUnread(0);
    try {
      await api.notifications.readAll();
    } catch {
      /* ignore */
    }
  };

  const timeAgo = (iso: string | null) => {
    if (!iso) return "";
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  };

  return (
    <div ref={panelRef} className="relative">
      <button
        onClick={() => {
          setOpen((o) => !o);
          syncPushState();
        }}
        className="relative p-2 rounded-xl text-slate-500 hover:bg-slate-100 hover:text-slate-800 transition-colors"
        aria-label="Notifications"
      >
        <Bell className="w-5 h-5" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full bg-gradient-to-r from-red-500 to-pink-500 text-white text-[10px] font-semibold flex items-center justify-center">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-12 w-96 max-h-[70vh] overflow-y-auto scrollbar-thin bg-white rounded-2xl border border-slate-200 shadow-xl z-50">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 sticky top-0 bg-white rounded-t-2xl">
            <h3 className="font-semibold text-slate-800">Reminders</h3>
            {unread > 0 && (
              <button onClick={readAll} className="text-xs text-pink-600 hover:text-pink-700 font-medium flex items-center gap-1">
                <CheckCheck className="w-3.5 h-3.5" /> Mark all read
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <p className="text-sm text-slate-500 text-center py-10">No reminders yet</p>
          ) : (
            items.map((n) => (
              <button
                key={n.id}
                onClick={() => markRead(n.id)}
                className={`w-full text-left px-4 py-3 border-b border-slate-50 hover:bg-slate-50 transition-colors ${n.is_read ? "opacity-50" : ""}`}
              >
                <div className="flex items-start gap-2.5">
                  <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${PRIORITY_COLOR[n.priority] || "bg-amber-400"} ${n.is_read ? "opacity-40" : ""}`} />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate">{n.title}</p>
                    {n.message && <p className="text-xs text-slate-500 mt-0.5 line-clamp-2">{n.message}</p>}
                    <p className="text-[11px] text-slate-400 mt-1">{timeAgo(n.created_at)}</p>
                  </div>
                </div>
              </button>
            ))
          )}
          <div className="px-4 py-3 border-t border-slate-100 sticky bottom-0 bg-white rounded-b-2xl">
            <button
              onClick={togglePush}
              disabled={pushBusy}
              className={`w-full flex items-center justify-center gap-2 text-xs font-semibold py-2 rounded-lg transition-colors ${
                pushOn
                  ? "text-pink-600 hover:bg-pink-50"
                  : "text-white bg-gradient-to-r from-indigo-500 to-pink-500 hover:opacity-90"
              }`}
            >
              <BellRing className="w-3.5 h-3.5" />
              {pushBusy ? "Working…" : pushOn ? "Push notifications on — tap to turn off" : "Enable deadline notifications"}
            </button>
            {!pushOn && "Notification" in window && Notification.permission === "denied" && (
              <p className="text-[11px] text-red-500 text-center mt-1.5">
                Notifications are blocked in your browser settings.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
