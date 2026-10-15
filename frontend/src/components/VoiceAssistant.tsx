"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, MicOff, Volume2, VolumeX, X, Sparkles, Loader2, Send, Play, Pause } from "lucide-react";
import { clsx } from "clsx";
import { api } from "@/lib/api";

type RecognitionResult = {
  transcript: string;
  isFinal?: boolean;
};

type RecognitionEvent = {
  results: ArrayLike<ArrayLike<RecognitionResult>>;
};

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

const WAKEWORDS = ["hey chief", "ok chief", "okay chief", "hello chief", "hi chief", "chief"];

function toISODate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatDay(isoDate: string): string {
  const d = new Date(`${isoDate}T12:00:00`);
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

function formatDeadline(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function containsWakeWord(text: string): boolean {
  const t = text.toLowerCase();
  return WAKEWORDS.some((w) => t.includes(w));
}

function stripWakeWord(text: string): string {
  const lower = text.toLowerCase();
  for (const w of WAKEWORDS) {
    const idx = lower.indexOf(w);
    if (idx >= 0) return text.slice(idx + w.length).replace(/^[,.\s]+/, "");
  }
  return text;
}

function routeIntent(text: string): string {
  if (/help|what can you|what do you do/.test(text)) return "help";
  if (/^speak$|^test$|^hello$|^hi$|can you hear me|are you there/.test(text)) return "speak-test";
  if (/brief|overview|summary of my day/.test(text)) return "briefing";
  if (/tomorrow|next day/.test(text)) return "tomorrow";
  if (/deadline|urgent/.test(text)) return "emails-deadline";
  if (/news|headline/.test(text)) return "news";
  if (/today|schedule|calendar|meeting|what do i have|what's on|what is on/.test(text)) return "today";
  if (/timetable|classes|lecture|class schedule/.test(text)) return "timetable";
  if (/email/.test(text)) return "emails";
  if (/task|to ?do|to-do|pending/.test(text)) return "tasks";
  if (/exam/.test(text)) return "exams";
  if (/news|headline/.test(text)) return "news";
  if (/dashboard|status/.test(text)) return "dashboard";
  return "unknown";
}

async function runIntent(intent: string): Promise<string> {
  switch (intent) {
    case "speak-test":
      return "Hello! I am Chief, your AI voice assistant. I can hear you clearly. What would you like to know?";
    case "briefing": {
      const b = await api.briefing.today();
      const parts: string[] = [];
      if (b.summary) parts.push(b.summary);
      if (b.meetings_count) parts.push(`You have ${b.meetings_count} meeting${b.meetings_count === 1 ? "" : "s"} today.`);
      if (b.important_emails_count) parts.push(`${b.important_emails_count} important email${b.important_emails_count === 1 ? "" : "s"}.`);
      if (b.pending_tasks_count) parts.push(`${b.pending_tasks_count} pending task${b.pending_tasks_count === 1 ? "" : "s"}.`);
      if (b.suggested_plan) parts.push(b.suggested_plan);
      return parts.length ? parts.join(" ") : "No briefing available right now.";
    }
    case "today": {
      const today = toISODate(new Date());
      const events = await api.events.list({ date_from: today, date_to: today });
      if (!events.length) return "You have no events scheduled today.";
      const listed = events.map((e) => `${e.title} at ${formatTime(e.start_time)}`);
      return `Today you have ${events.length} event${events.length === 1 ? "" : "s"}: ${listed.join(". ")}.`;
    }
    case "tomorrow": {
      const d = new Date();
      d.setDate(d.getDate() + 1);
      const date = toISODate(d);
      const events = await api.events.list({ date_from: date, date_to: date });
      if (!events.length) return "You have no events scheduled tomorrow.";
      const listed = events.map((e) => `${e.title} at ${formatTime(e.start_time)}`);
      return `Tomorrow you have ${events.length} event${events.length === 1 ? "" : "s"}: ${listed.join(". ")}.`;
    }
    case "timetable": {
      const entries = await api.timetable.list();
      const dow = new Date().getDay();
      const todays = entries.filter((e) => e.day_of_week === dow && e.active);
      if (!todays.length) return "No classes are scheduled today.";
      const parts = todays.map((e) => `${e.title} from ${e.start_time} to ${e.end_time}${e.location ? ` in ${e.location}` : ""}`);
      return `Today's timetable: ${parts.join(". ")}.`;
    }
    case "emails": {
      const stats = await api.emails.stats();
      return `You have ${stats.total} emails total, ${stats.unread} unread, ${stats.reply_required} need a reply, and ${stats.important} are important.`;
    }
    case "emails-deadline": {
      const emails = await api.emails.list({ has_deadline: true, limit: 5 });
      if (!emails.length) return "No emails with deadlines right now.";
      const parts = emails.map((e) => `${e.subject}${e.deadline_at ? `, due ${formatDeadline(e.deadline_at)}` : ""}`);
      return `${emails.length} email${emails.length === 1 ? "" : "s"} with deadlines: ${parts.join("; ")}.`;
    }
    case "tasks": {
      const tasks = await api.tasks.list();
      const pending = tasks.filter((t) => t.status === "pending" || t.status === "in_progress");
      if (!pending.length) return "You have no pending tasks.";
      const top = pending.slice(0, 5).map((t) => t.title);
      return `${pending.length} pending task${pending.length === 1 ? "" : "s"}: ${top.join(", ")}.`;
    }
    case "exams": {
      const exams = await api.exams.list();
      const today = toISODate(new Date());
      const upcoming = exams
        .filter((e) => e.exam_date && e.exam_date >= today)
        .sort((a, b) => a.exam_date.localeCompare(b.exam_date!))
        .slice(0, 5);
      if (!upcoming.length) return "You have no upcoming exams.";
      const parts = upcoming.map((e) => `${e.title} on ${formatDay(e.exam_date)}`);
      return `Your next exams: ${parts.join(". ")}.`;
    }
    case "news": {
      const res = await api.news.list("technology", 3);
      if (!res.items.length) return "No news available right now.";
      const headlines = res.items.map((i) => i.title);
      return `Here are the latest headlines: ${headlines.join(". ")}.`;
    }
    case "dashboard": {
      const d = await api.dashboard.get();
      const parts: string[] = [];
      parts.push(`You have ${d.today_events} event${d.today_events === 1 ? "" : "s"} today.`);
      parts.push(`${d.task_stats.pending} pending task${d.task_stats.pending === 1 ? "" : "s"}.`);
      if (d.deadline_count) parts.push(`${d.deadline_count} deadline${d.deadline_count === 1 ? "" : "s"} coming up.`);
      if (d.overdue_deadline_count) parts.push(`${d.overdue_deadline_count} overdue.`);
      if (d.important_emails?.length) parts.push(`${d.important_emails.length} important email${d.important_emails.length === 1 ? "" : "s"}.`);
      return parts.join(" ");
    }
    case "help":
      return "I can read your daily briefing, today's or tomorrow's schedule, timetable, emails, tasks, exams, news, and dashboard. Try asking what do I have today, or read my briefing.";
    default:
      return "I didn't quite catch that. Try asking about your briefing, schedule, timetable, emails, tasks, exams, or news.";
  }
}

function playBeep(kind: "wake" | "done" | "error") {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    void ctx.resume?.();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = kind === "wake" ? 880 : kind === "done" ? 660 : 330;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2);
    osc.start();
    osc.stop(ctx.currentTime + 0.2);
    osc.onended = () => ctx.close();
  } catch {
    // audio not available
  }
}

export default function VoiceAssistant() {
  const [open, setOpen] = useState(false);
  const [handsFree, setHandsFree] = useState(false);
  const [muted, setMuted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [paused, setPaused] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [support, setSupport] = useState<{ secure: boolean; recog: boolean; perm: string } | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mutedRef = useRef(false);
  const handsFreeRef = useRef(false);
  const armedRef = useRef<"wake" | "command" | null>(null);
  const busyRef = useRef(false);
  const pendingTextRef = useRef("");
  const settleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speakingRef = useRef(false);
  const pausedRef = useRef(false);

  useEffect(() => {
    mutedRef.current = muted;
  }, [muted]);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    handsFreeRef.current = handsFree;
    if (handsFree) {
      setError(null);
      armedRef.current = "wake";
      const rec = recognitionRef.current;
      if (!rec) return;
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((stream) => {
          stream.getTracks().forEach((t) => t.stop());
          startRecognizer();
        })
        .catch(() => {
          setListening(false);
          setError("Microphone access denied. Allow the mic in the browser prompt and try again.");
        });
    } else {
      armedRef.current = null;
      stopRecognizer();
      setListening(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handsFree]);

  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    const load = () => {
      voicesRef.current = window.speechSynthesis.getVoices?.() ?? [];
    };
    load();
    window.speechSynthesis.addEventListener?.("voiceschanged", load);
    return () => window.speechSynthesis.removeEventListener?.("voiceschanged", load);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const unlock = () => {
      try {
        const synth = window.speechSynthesis;
        if (synth) {
          synth.cancel();
          synth.resume?.();
          synth.speak(new SpeechSynthesisUtterance(""));
        }
        const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (Ctx) void new Ctx();
      } catch {
        // ignore
      }
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  const speak = useCallback(
    (text: string, onDone?: () => void) => {
      const synth = window.speechSynthesis;
      if (mutedRef.current || typeof window === "undefined" || !synth || !text) {
        onDone?.();
        return;
      }
      try {
        speakingRef.current = true;
        setSpeaking(true);
        pausedRef.current = false;
        setPaused(false);
        try {
          recognitionRef.current?.stop();
        } catch {
          /* ignore */
        }
        setListening(false);
        synth.cancel();
        synth.resume?.();
        const utter = new SpeechSynthesisUtterance(text);
        utter.rate = 0.98;
        utter.pitch = 1;
        const voices = voicesRef.current.length ? voicesRef.current : synth.getVoices?.() ?? [];
        const voice =
          voices.find((v) => v.lang.startsWith("en") && v.lang.endsWith("IN")) ??
          voices.find((v) => v.lang.startsWith("en"));
        if (voice) utter.voice = voice;
        utter.onend = () => {
          speakingRef.current = false;
          setSpeaking(false);
          pausedRef.current = false;
          setPaused(false);
          onDone?.();
        };
        utter.onerror = (e) => {
          speakingRef.current = false;
          setSpeaking(false);
          pausedRef.current = false;
          setPaused(false);
          console.error("SpeechSynthesis error:", e);
          onDone?.();
        };
        synth.speak(utter);
      } catch (e) {
        speakingRef.current = false;
        setSpeaking(false);
        pausedRef.current = false;
        setPaused(false);
        console.error("SpeechSynthesis failed:", e);
        onDone?.();
      }
    },
    []
  );

  const startRecognizer = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec || busyRef.current) return;
    setListening(true);
    try {
      rec.continuous = !!handsFreeRef.current;
      rec.start();
    } catch {
      setListening(false);
    }
  }, []);

  const stopRecognizer = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
  }, []);

  const handleTranscript = useCallback(
    async (text: string) => {
      setTranscript(text);
      setError(null);
      setBusy(true);
      busyRef.current = true;
      try {
        const result = await runIntent(routeIntent(text));
        setReply(result);
        if (!mutedRef.current) playBeep("done");
        speak(result, () => {
          if (handsFreeRef.current) {
            armedRef.current = "wake";
            setTimeout(() => {
              if (handsFreeRef.current && !busyRef.current) startRecognizer();
            }, 300);
          }
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Something went wrong. Try again.";
        setReply(msg);
        setError(msg);
        if (!mutedRef.current) playBeep("error");
        if (handsFreeRef.current) {
          armedRef.current = "wake";
          setTimeout(() => {
            if (handsFreeRef.current && !busyRef.current) startRecognizer();
          }, 300);
        }
      } finally {
        setBusy(false);
        busyRef.current = false;
      }
    },
    [speak, startRecognizer]
  );

  const handleTranscriptRef = useRef(handleTranscript);
  useEffect(() => {
    handleTranscriptRef.current = handleTranscript;
  }, [handleTranscript]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const w = window as unknown as {
      SpeechRecognition?: new () => SpeechRecognitionLike;
      webkitSpeechRecognition?: new () => SpeechRecognitionLike;
    };
    const recog = !!(w.SpeechRecognition || w.webkitSpeechRecognition);
    const secure = window.isSecureContext === true;
    const readPerm = () => {
      navigator.permissions?.query({ name: "microphone" as PermissionName })
        .then((s) => setSupport({ secure, recog, perm: s.state }))
        .catch(() => setSupport({ secure, recog, perm: "unknown" }));
    };
    readPerm();
    const timer = setTimeout(readPerm, 3000);
    return () => clearTimeout(timer);
  }, []);

  const createRecognizer = useCallback(() => {
    const w = window as unknown as {
      SpeechRecognition?: new () => SpeechRecognitionLike;
      webkitSpeechRecognition?: new () => SpeechRecognitionLike;
    };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) return null;
    const rec = new Ctor();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = !!handsFreeRef.current;
    rec.onstart = () => setListening(true);
    rec.onresult = (event) => {
      const results = event.results;
      const last = results[results.length - 1];
      const text = last?.[0]?.transcript?.trim() ?? "";
      if (!text) return;

      if (armedRef.current === "wake") {
        if (containsWakeWord(text)) {
          const command = stripWakeWord(text);
          if (!mutedRef.current) playBeep("wake");
          armedRef.current = "command";
          if (command) {
            setTranscript(command);
            rec.stop();
            handleTranscriptRef.current(command);
          } else {
            setTranscript("Yes?");
            speak("Yes? How can I help?", () => {
              armedRef.current = "command";
              setListening(true);
              try {
                rec.continuous = true;
                rec.start();
              } catch {
                /* ignore */
              }
            });
          }
        }
        return;
      }

      pendingTextRef.current = text;
      setTranscript(text);

      if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
      settleTimerRef.current = setTimeout(() => {
        const t = pendingTextRef.current.trim();
        if (!t) return;
        pendingTextRef.current = "";
        try {
          rec.stop();
        } catch {
          /* ignore */
        }
        const command = stripWakeWord(t);
        handleTranscriptRef.current(command || t);
      }, 1200);
    };
    rec.onerror = (event) => {
      if (event.error === "not-allowed") {
        setListening(false);
        setError("Microphone access denied. Allow the mic in the browser prompt and try again.");
      } else if (event.error === "no-speech") {
        setListening(false);
        setError("I didn't hear anything. Please speak and try again.");
      } else if (event.error === "network") {
        setError("Speech service unavailable (network). Check your connection.");
      } else if (event.error === "audio-capture") {
        setListening(false);
        setError("No microphone found. Check your mic is plugged in and Chrome has mic access (Site Settings).");
      }
    };
    rec.onend = () => {
      if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
      const leftover = pendingTextRef.current.trim();
      pendingTextRef.current = "";
      if (speakingRef.current) return;
      if (leftover && armedRef.current !== "wake" && !busyRef.current) {
        const command = stripWakeWord(leftover);
        handleTranscriptRef.current(command || leftover);
        return;
      }
      if (handsFreeRef.current && !busyRef.current) {
        setTimeout(() => {
          if (handsFreeRef.current && !busyRef.current) startRecognizer();
        }, 250);
      } else {
        setListening(false);
      }
    };
    return rec;
  }, [speak, startRecognizer]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const w = window as unknown as {
      SpeechRecognition?: new () => SpeechRecognitionLike;
      webkitSpeechRecognition?: new () => SpeechRecognitionLike;
    };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) {
      setError("Voice input isn't supported in this browser. Try Chrome or Edge.");
      return;
    }
    if (window.isSecureContext !== true) {
      setError("Voice input requires localhost or HTTPS. Open http://localhost:3000 in Chrome.");
      return;
    }
    recognitionRef.current = createRecognizer();
    return () => {
      if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
      recognitionRef.current?.abort();
      recognitionRef.current = null;
      window.speechSynthesis?.cancel();
    };
  }, [createRecognizer]);

  const tapToTalk = useCallback(() => {
    setTranscript("");
    setReply("");
    setError(null);
    armedRef.current = null;
    const rec = createRecognizer();
    if (!rec) {
      setError("Voice input isn't supported in this browser. Try Chrome or Edge.");
      return;
    }
    recognitionRef.current = rec;
    setListening(true);
    try {
      rec.start();
    } catch {
      setListening(false);
      setError("Could not start voice recognition. Check Chrome's mic permission.");
    }
  }, [createRecognizer]);

  const stopListening = useCallback(() => {
    stopRecognizer();
  }, [stopRecognizer]);

  const submitInput = useCallback(() => {
    const t = input.trim();
    if (!t) return;
    setInput("");
    handleTranscriptRef.current(t);
  }, [input]);

  const statusText = handsFree
    ? armedRef.current === "command"
      ? "Listening for your command..."
      : 'Hands-free listening — say "Hey Chief"'
    : "Tap the mic and speak";

  return (
    <>
      {open ? (
        <div className="fixed bottom-4 right-4 z-50 w-[360px] max-w-[calc(100vw-2rem)] bg-white rounded-2xl shadow-2xl shadow-slate-900/20 border border-slate-200 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 bg-instagram">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-white" />
              <span className="text-sm font-bold text-white">Voice Assistant</span>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setHandsFree((h) => !h)}
                className={clsx(
                  "flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-semibold transition-colors",
                  handsFree ? "bg-white/25 text-white" : "bg-white/10 text-white/80 hover:text-white"
                )}
                aria-label={handsFree ? "Turn off hands-free mode" : "Turn on hands-free mode"}
              >
                <span className={clsx("relative w-6 h-3.5 rounded-full transition-colors", handsFree ? "bg-emerald-400" : "bg-white/30")}>
                  <span
                    className={clsx(
                      "absolute top-0.5 w-2.5 h-2.5 rounded-full bg-white transition-all",
                      handsFree ? "left-3.5" : "left-0.5"
                    )}
                  />
                </span>
                Hands-free
              </button>
              <button
                onClick={() => setMuted((m) => !m)}
                className="p-1.5 text-white/80 hover:text-white"
                aria-label={muted ? "Unmute voice replies" : "Mute voice replies"}
              >
                {muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
              </button>
              <button
                onClick={() => setOpen(false)}
                className="p-1.5 text-white/80 hover:text-white"
                aria-label="Close voice assistant"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="px-4 py-4 min-h-[104px] max-h-64 overflow-y-auto space-y-3">
            {transcript && (
              <p className="text-sm text-slate-600 italic">
                <span className="font-medium text-slate-900 not-italic">You:</span> {transcript}
              </p>
            )}
            {reply && (
              <p className="text-sm text-slate-700">
                <span className="font-medium text-slate-900">Chief:</span> {reply}
              </p>
            )}
            {busy && (
              <p className="text-sm text-slate-400 flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" /> Thinking...
              </p>
            )}
            {handsFree && !busy && (
              <p className="text-sm text-pink-600 flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-pink-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-pink-600" />
                </span>
                {statusText}
              </p>
            )}
            {!handsFree && listening && !busy && !transcript && (
              <p className="text-sm text-pink-600 flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-pink-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-pink-600" />
                </span>
                Listening... speak now
              </p>
            )}
            {error && !busy && <p className="text-sm text-red-500">{error}</p>}
            {support && !error && !busy && (
              <p className="text-[11px] text-slate-400">
                Browser check: mic permission: <span className="font-medium text-slate-600">{support.perm}</span> · speech API:{" "}
                <span className={support.recog ? "text-emerald-600" : "text-red-500"}>{support.recog ? "yes" : "NO"}</span> · secure page:{" "}
                <span className={support.secure ? "text-emerald-600" : "text-red-500"}>{support.secure ? "yes" : "NO"}</span>
              </p>
            )}
            {!transcript && !reply && !busy && !handsFree && !error && (
              <p className="text-sm text-slate-400">
                Try: "read my briefing", "what do I have today", "any emails with deadlines", "what's on tomorrow". Or enable hands-free and say "Hey Chief".
              </p>
            )}
          </div>

          <div className="px-4 pb-4 space-y-3">
            <div className="flex items-center gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submitInput()}
                placeholder='Type a command, e.g. "read my briefing"'
                className="flex-1 rounded-xl border border-slate-200 px-3 py-2.5 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-pink-500/40"
              />
              <button
                onClick={submitInput}
                className="p-2.5 rounded-xl bg-slate-100 text-slate-600 hover:text-pink-600 hover:bg-slate-200"
                aria-label="Send command"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
            {speaking && (
              <button
                onClick={() => {
                  const synth = window.speechSynthesis;
                  if (!synth) return;
                  if (paused) {
                    synth.resume();
                    pausedRef.current = false;
                    setPaused(false);
                  } else {
                    synth.pause();
                    pausedRef.current = true;
                    setPaused(true);
                  }
                }}
                className="w-full flex items-center justify-center gap-2 rounded-xl py-2.5 text-sm font-semibold text-white bg-amber-500 shadow-md shadow-amber-500/25 transition-all"
              >
                {paused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
                {paused ? "Resume" : "Pause"}
              </button>
            )}
            {handsFree ? (
              <button
                onClick={stopListening}
                className={clsx(
                  "w-full flex items-center justify-center gap-2 rounded-xl py-2.5 text-sm font-semibold text-white shadow-md transition-all",
                  listening ? "bg-red-500 shadow-red-500/25" : "bg-slate-400"
                )}
              >
                {listening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
                {listening ? "Stop hands-free listening" : "Hands-free paused"}
              </button>
            ) : (
              <button
                onClick={tapToTalk}
                className="w-full flex items-center justify-center gap-2 rounded-xl py-2.5 text-sm font-semibold text-white shadow-md shadow-pink-600/25 bg-instagram transition-all"
              >
                {listening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
                {listening ? "Stop listening" : "Tap to speak"}
              </button>
            )}
          </div>
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          aria-label="Open voice assistant"
          className="fixed bottom-4 right-4 z-50 w-14 h-14 rounded-2xl bg-instagram text-white flex items-center justify-center shadow-lg shadow-pink-600/30 hover:scale-105 transition-transform"
        >
          {handsFree ? <Sparkles className="w-6 h-6" /> : <Mic className="w-6 h-6" />}
        </button>
      )}
    </>
  );
}
