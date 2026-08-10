function getBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (configured) return configured;
  if (typeof window === "undefined") return "http://localhost:8000";
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") return "http://localhost:8000";
  return `http://${host}:8000`;
}

const BASE_URL = getBaseUrl();
const TOKEN_KEY = "aichief_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
  window.dispatchEvent(new Event("aichief:auth"));
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let lastError: unknown = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const token = getToken();
      const res = await fetch(`${BASE_URL}${path}`, {
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...options?.headers,
        },
        ...options,
      });
      if (res.status === 401) {
        setToken(null);
        throw new Error("Session expired. Please sign in again.");
      }
      if (!res.ok) {
        const err = await res.text();
        throw new Error(`API Error ${res.status}: ${err}`);
      }
      return res.json();
    } catch (e) {
      lastError = e;
      if (attempt < 2 && isNetworkError(e)) {
        await new Promise((r) => setTimeout(r, 600 * (attempt + 1)));
        continue;
      }
      break;
    }
  }
  if (isNetworkError(lastError)) {
    throw new Error(
      "Can't reach the AI Chief server (localhost:8000). Make sure the backend is running, then refresh."
    );
  }
  throw lastError;
}

function isNetworkError(e: unknown): boolean {
  return e instanceof TypeError || (e instanceof Error && e.message.includes("fetch"));
}

export interface EmailStats {
  total: number;
  unread: number;
  reply_required: number;
  important: number;
  with_deadlines: number;
  can_ignore: number;
}

export interface NewsItem {
  title: string;
  link: string;
  summary: string;
  source: string | null;
  published_at: string | null;
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
  recurrence_rule: string | null;
  recurrence_until: string | null;
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

export interface DashboardDeadline {
  title: string;
  kind: string;
  course: string | null;
  due_at: string;
  overdue: boolean;
}

export interface DashboardImportantEmail {
  id: number;
  subject: string;
  sender: string | null;
  category: string;
}

export interface GoogleAuthHealth {
  connected: boolean;
  state: string;
  reason: string | null;
  last_error: string | null;
  expires_at: string | null;
  last_checked_at: string | null;
  refresh_attempts: number;
  email?: string | null;
  has_refresh_token?: boolean;
  scopes?: string[];
}

export interface Dashboard {
  email_stats: EmailStats;
  today_events: number;
  calendar_conflicts: number;
  task_stats: TaskStats;
  documents_count: number;
  upcoming_deadlines: DashboardDeadline[];
  overdue_deadline_count: number;
  deadline_count: number;
  deadline_this_week: number;
  important_emails: DashboardImportantEmail[];
  student_email: string | null;
  google_auth?: GoogleAuthHealth;
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

export interface TimetableEntry {
  id: number;
  title: string;
  location: string | null;
  day_of_week: number;
  day_name: string;
  start_time: string;
  end_time: string;
  source: string;
  active: boolean;
}

export interface ClassroomAssignment {
  id: string;
  course_id: string;
  course_name: string;
  title: string;
  description: string | null;
  work_type: string | null;
  state: string | null;
  due: string | null;
  max_points: number | null;
}

export interface Exam {
  id: number;
  title: string;
  subject: string | null;
  exam_date: string;
  duration_minutes: number | null;
  location: string | null;
  notes: string | null;
  status: string;
  score: number | null;
  max_score: number | null;
}

export interface Assignment {
  id: number;
  title: string;
  course: string | null;
  due_date: string | null;
  status: string;
  points_earned: number | null;
  points_max: number | null;
  priority: string;
  source: string;
  source_id: string | null;
  external_url: string | null;
  created_at: string | null;
}

export interface AttendanceRecord {
  id: number;
  course: string;
  date: string;
  status: string;
  notes: string | null;
}

export interface AttendanceStats {
  total: number;
  present: number;
  absent: number;
  late: number;
  excused: number;
  percentage: number;
}

// Dashboard
export const api = {
  auth: {
    login: (password: string, emergencyCode?: string) =>
      request<{ token: string; expires_in: number }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          password,
          ...(emergencyCode ? { emergency_code: emergencyCode } : {}),
        }),
      }),
    status: () => request<{ enabled: boolean; authenticated: boolean }>("/api/auth/status"),
  },

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
    sync: (limit = 50) => request<{ added: number }>(`/api/emails/sync?limit=${limit}`, { method: "POST" }),
    processAll: (limit = 50) =>
      request<{ processed: number; failed: number; remaining: number }>(`/api/emails/process-all?limit=${limit}`, { method: "POST" }),
    health: () => request<GoogleAuthHealth>("/api/emails/health"),
    reauth: () => request<{ connected: boolean }>("/api/emails/re-auth", { method: "POST" }),
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
    scheduleStudy: (dateFrom: string, dateTo?: string, minutesPerDay = 60, subject?: string, overwrite = false) => {
      const params = new URLSearchParams({ date_from: dateFrom, minutes_per_day: String(minutesPerDay) });
      if (dateTo) params.set("date_to", dateTo);
      if (subject) params.set("subject", subject);
      if (overwrite) params.set("overwrite", "true");
      return request<{ date_from: string; date_to: string; minutes_per_day: number; created: number; per_day: Record<string, number> }>(
        `/api/events/schedule-study?${params}`, { method: "POST" }
      );
    },
    syncToGoogle: (dateFrom: string, dateTo?: string) => {
      const params = new URLSearchParams({ date_from: dateFrom });
      if (dateTo) params.set("date_to", dateTo);
      return request<{ date_from: string; date_to: string; created: number; skipped: number; failed: number }>(
        `/api/events/sync-to-google?${params}`, { method: "POST" }
      );
    },
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
      const token = getToken();
      const res = await fetch(`${BASE_URL}/api/documents/upload`, {
        method: "POST",
        body: form,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
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
    today: (refresh = false) => request<Briefing>(`/api/briefing/today${refresh ? "?refresh=1" : ""}`),
  },

  // Timetable
  timetable: {
    list: () => request<TimetableEntry[]>("/api/timetable"),
    create: (data: Partial<TimetableEntry>) =>
      request<TimetableEntry>("/api/timetable", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<TimetableEntry>) =>
      request<TimetableEntry>(`/api/timetable/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => request<{ ok: boolean }>(`/api/timetable/${id}`, { method: "DELETE" }),
    schedule: (dateFrom: string, dateTo?: string, overwrite = false) => {
      const params = new URLSearchParams({ date_from: dateFrom });
      if (dateTo) params.set("date_to", dateTo);
      if (overwrite) params.set("overwrite", "true");
      return request<{ date_from: string; date_to: string; created: number; skipped: number }>(
        `/api/timetable/schedule?${params}`, { method: "POST" }
      );
    },
  },

  // Google Classroom
  classroom: {
    status: () => request<{ connected: boolean; reason: string }>("/api/classroom/status"),
    courses: () => request<Array<{ id: string; name: string; section: string | null; room: string | null }>>("/api/classroom/courses"),
    assignments: () => request<ClassroomAssignment[]>("/api/classroom/assignments"),
    sync: () => request<{ assignments: number; tasks_created: number; tasks_skipped: number; events_created: number; events_skipped: number }>(
      "/api/classroom/sync", { method: "POST" }
    ),
  },

  // Microsoft Teams / Outlook
  teams: {
    status: () => request<{ configured: boolean; connected: boolean; account?: string; reason: string }>("/api/teams/status"),
    connect: () => request<{ user_code: string; verification_uri: string; message: string; expires_in: number }>(
      "/api/teams/connect", { method: "POST" }
    ),
    poll: () => request<{ status: string; account?: string; detail?: string }>("/api/teams/connect/poll", { method: "POST" }),
    sync: () => request<{ fetched: number; created: number; skipped: number }>("/api/teams/sync", { method: "POST" }),
  },

  // Exams
  exams: {
    list: (status?: string) => request<Exam[]>(`/api/exams${status ? `?status=${status}` : ""}`),
    create: (data: Partial<Exam>) => request<Exam>("/api/exams", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Exam>) =>
      request<Exam>(`/api/exams/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => request<{ ok: boolean }>(`/api/exams/${id}`, { method: "DELETE" }),
  },

  // Assignments
  assignments: {
    list: (status?: string) => request<Assignment[]>(`/api/assignments${status ? `?status=${status}` : ""}`),
    create: (data: Partial<Assignment>) =>
      request<Assignment>("/api/assignments", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Assignment>) =>
      request<Assignment>(`/api/assignments/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => request<{ ok: boolean }>(`/api/assignments/${id}`, { method: "DELETE" }),
    syncClassroom: () =>
      request<{ fetched: number; created: number; skipped: number }>("/api/assignments/sync-classroom", { method: "POST" }),
  },

  // Attendance
  attendance: {
    list: (course?: string) => request<AttendanceRecord[]>(`/api/attendance${course ? `?course=${encodeURIComponent(course)}` : ""}`),
    create: (data: { course: string; date: string; status: string; notes?: string }) =>
      request<AttendanceRecord>("/api/attendance", { method: "POST", body: JSON.stringify(data) }),
    delete: (id: number) => request<{ ok: boolean }>(`/api/attendance/${id}`, { method: "DELETE" }),
    stats: () => request<AttendanceStats>("/api/attendance/stats"),
  },

  // News
  news: {
    list: (topic: string = "technology", limit = 20) =>
      request<{ topic: string; items: NewsItem[] }>(`/api/news?topic=${topic}&limit=${limit}`),
  },

  // Notifications
  notifications: {
    list: (limit = 30, unreadOnly = false) => {
      const q = new URLSearchParams({ limit: String(limit) });
      if (unreadOnly) q.set("unread_only", "true");
      return request<Array<{ id: number; type: string; title: string; message: string; entity_type: string | null; entity_id: number | null; priority: string; is_read: boolean; created_at: string | null }>>(
        `/api/notifications?${q}`
      );
    },
    unreadCount: () => request<{ count: number }>("/api/notifications/unread-count"),
    markRead: (id: number) => request<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: "POST" }),
    readAll: () => request<{ ok: boolean }>("/api/notifications/read-all", { method: "POST" }),
    vapidPublicKey: () => request<{ key: string }>("/api/notifications/vapid-public-key"),
    subscribe: (sub: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
      request<{ ok: boolean }>("/api/notifications/subscribe", {
        method: "POST",
        body: JSON.stringify(sub),
      }),
  },
};
