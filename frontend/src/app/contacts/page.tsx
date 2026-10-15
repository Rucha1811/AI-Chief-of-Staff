"use client";

import { useEffect, useState } from "react";
import { api, Contact } from "@/lib/api";
import { RefreshCw, Users } from "lucide-react";
import BackButton from "@/components/BackButton";

export default function ContactsPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setContacts(await api.contacts.list());
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load contacts");
    }
  };

  useEffect(() => { load(); }, []);

  const sync = async () => {
    setBusy(true);
    try {
      await api.contacts.sync();
      await load();
    } catch (err: any) {
      setError(err.message || "Contact sync failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6 max-w-4xl">
      <BackButton />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gradient mt-3 flex items-center gap-2"><Users className="w-6 h-6 text-pink-500" /> Contacts</h1>
          <p className="text-slate-500 mt-1">{contacts.length} Google contacts synced</p>
        </div>
        <button onClick={sync} disabled={busy} className="btn-primary flex items-center gap-2">
          <RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} /> {busy ? "Syncing…" : "Sync Google Contacts"}
        </button>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="grid gap-3">
        {contacts.map((contact) => (
          <div key={contact.id} className="card">
            <h2 className="font-semibold text-slate-800">{contact.display_name}</h2>
            {contact.emails.length > 0 && <p className="text-sm text-slate-500">{contact.emails.join(", ")}</p>}
            {contact.phone_numbers.length > 0 && <p className="text-sm text-slate-500">{contact.phone_numbers.join(", ")}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
