const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API Error ${res.status}: ${err}`);
  }
  return res.json();
}

export interface EmailStats {
  total: number;
  unread: number;
  reply_required: number;
  important: number;
  with_deadlines: number;
  can_ignore: number;
}

export interface Email {
  id: number;
  message_id: string;
  thread_id: string | null;
  from_email: string;
  from_name: string | null;
  to_email: string | null;
  subject: string;
  body_text: string | null;
  received_at: string | null;
  category: string;
  is_read: boolean;
  is_starred: boolean;
  has_deadline: boolean;
  deadline_at: string | null;
  deadline_description: string | null;
  suggested_reply: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface Event {
  id: number;
  calendar_event_id: string | null;
  title: string;
  description: string | null;
  location: string | null;
  start_time: string;
  end_time: string;
  is_all_day: boolean;
  is_conflict: boolean;
  conflict_description: string | null;
  suggested_action: string | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface FreeSlot {
  start: string;
  end: string;
  duration_minutes: number;
}

export interface Task {
  id: number;
  title: string;
  description: string | null;
  priority: string;
  status: string;
  source: string;
  source_id: number | null;
  due_date: string | null;
  estimated_minutes: number | null;
  is_recurring: boolean;
  recurrence_rule: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskStats {
  total: number;
  pending: number;
  in_progress: number;
  completed: number;
  overdue: number;
}

export interface Dashboard {
  email_stats: EmailStats;
  today_events: number;
  calendar_conflicts: number;
  task_stats: TaskStats;
  documents_count: number;
  date: string;
}

export interface Document {
  id: number;
  filename: string;
  file_type: string;
  file_size: number;
  summary: string | null;
  key_points: string | null;
  deadlines: string | null;
  is_indexed: boolean;
  created_at: string;
  updated_at: string;
}

export interface Briefing {
  greeting: string;
  summary: string;
  meetings_count: number;
  important_emails_count: number;
  pending_tasks_count: number;
  news_items: Array<{ title: string; summary: string }>;
  suggested_plan: string;
  meetings_highlights?: string;
  email_highlights?: string;
  task_highlights?: string;
  full_briefing?: string;
}

// Dashboard
export const api = {
  dashboard: {
    get: () => request<Dashboard>("/api/dashboard"),
  },

  // Emails
  emails: {
    list: (params?: { category?: string; has_deadline?: boolean; limit?: number }) => {
      const q = new URLSearchParams();
      if (params?.category) q.set("category", params.category);
      if (params?.has_deadline !== undefined) q.set("has_deadline", String(params.has_deadline));
      if (params?.limit) q.set("limit", String(params.limit));
      return request<Email[]>(`/api/emails?${q}`);
    },
    stats: () => request<EmailStats>("/api/emails/stats"),
    get: (id: number) => request<Email>(`/api/emails/${id}`),
    classify: (id: number) => request<Email>(`/api/emails/${id}/classify`, { method: "POST" }),
    suggestReply: (id: number) =>
      request<{ email_id: number; suggested_reply: string }>(`/api/emails/${id}/suggest-reply`, { method: "POST" }),
    summarize: (id: number) =>
      request<{ email_id: number; summary: string; has_deadline: boolean; deadline_at: string | null; deadline_description: string | null }>(
        `/api/emails/${id}/summarize`, { method: "POST" }
      ),
    toggleStar: (id: number) => request<Email>(`/api/emails/${id}/star`, { method: "PATCH" }),
    markRead: (id: number) => request<Email>(`/api/emails/${id}/read`, { method: "PATCH" }),
  },

  // Events
  events: {
    list: (params?: { date_from?: string; date_to?: string }) => {
      const q = new URLSearchParams();
      if (params?.date_from) q.set("date_from", params.date_from);
      if (params?.date_to) q.set("date_to", params.date_to);
      return request<Event[]>(`/api/events?${q}`);
    },
    create: (data: Partial<Event>) =>
      request<Event>("/api/events", { method: "POST", body: JSON.stringify(data) }),
    delete: (id: number) => request<{ ok: boolean }>(`/api/events/${id}`, { method: "DELETE" }),
    detectConflicts: (params?: { date_from?: string; date_to?: string }) => {
      const q = new URLSearchParams();
      if (params?.date_from) q.set("date_from", params.date_from);
      if (params?.date_to) q.set("date_to", params.date_to);
      return request<Array<{ event1: string; event2: string; description: string; suggestion: string }>>(
        `/api/events/conflicts/detect?${q}`
      );
    },
    freeSlots: (date: string) => request<{ date: string; free_slots: FreeSlot[] }>(`/api/events/free-slots/${date}`),
  },

  // Tasks
  tasks: {
    list: (params?: { status?: string; priority?: string }) => {
      const q = new URLSearchParams();
      if (params?.status) q.set("status", params.status);
      if (params?.priority) q.set("priority", params.priority);
      return request<Task[]>(`/api/tasks?${q}`);
    },
    create: (data: { title: string; description?: string; priority?: string; due_date?: string }) =>
      request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: { status?: string; priority?: string }) =>
      request<Task>(`/api/tasks/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => request<{ ok: boolean }>(`/api/tasks/${id}`, { method: "DELETE" }),
    stats: () => request<TaskStats>("/api/tasks/stats"),
    reprioritize: () => request<Task[]>("/api/tasks/reprioritize", { method: "POST" }),
  },

  // Documents
  documents: {
    list: () => request<Document[]>("/api/documents"),
    upload: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${BASE_URL}/api/documents/upload`, { method: "POST", body: form });
      if (!res.ok) throw new Error(`Upload failed: ${await res.text()}`);
      return res.json() as Promise<Document>;
    },
    get: (id: number) => request<Document>(`/api/documents/${id}`),
    delete: (id: number) => request<{ ok: boolean }>(`/api/documents/${id}`, { method: "DELETE" }),
    summarize: (id: number) => request<Document>(`/api/documents/${id}/summarize`, { method: "POST" }),
    query: (documentId: number, question: string) =>
      request<{ document_id: number; question: string; answer: string }>("/api/documents/query", {
        method: "POST",
        body: JSON.stringify({ document_id: documentId, question }),
      }),
  },

  // Briefing
  briefing: {
    today: () => request<Briefing>("/api/briefing/today"),
  },
};
