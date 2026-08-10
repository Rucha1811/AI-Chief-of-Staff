"use client";

import { useEffect, useState } from "react";
import { api, NewsItem } from "@/lib/api";
import { Newspaper, ExternalLink, RefreshCw } from "lucide-react";
import BackButton from "@/components/BackButton";

const topics = [
  { id: "technology", label: "Technology" },
  { id: "top", label: "Top Stories" },
  { id: "business", label: "Business" },
  { id: "world", label: "World" },
  { id: "india", label: "India" },
];

function timeAgo(published: string | null): string {
  if (!published) return "";
  const date = new Date(published);
  const diff = Date.now() - date.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${Math.max(mins, 1)}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return date.toLocaleDateString();
}

export default function NewsPage() {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [topic, setTopic] = useState("technology");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async (t: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.news.list(t, 30);
      setItems(data.items);
    } catch (err: any) {
      setError(err.message || "Failed to load news");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(topic); }, [topic]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <BackButton />
          <h1 className="text-2xl font-bold text-gradient mt-3 flex items-center gap-2">
            <Newspaper className="w-6 h-6 text-transparent bg-instagram bg-clip-text" />
            Today&apos;s News
          </h1>
          <p className="text-slate-500 mt-1">Fresh headlines, curated for you</p>
        </div>
        <button onClick={() => load(topic)} className="btn-secondary flex items-center gap-2">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>

      <div className="flex gap-2 flex-wrap">
        {topics.map((t) => (
          <button
            key={t.id}
            onClick={() => setTopic(t.id)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-all ${
              topic === t.id
                ? "text-white bg-instagram shadow-md shadow-pink-600/30"
                : "text-slate-500 bg-white/5 border border-white/10 hover:text-slate-800"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-400" />
        </div>
      ) : error ? (
        <div className="card text-center py-12">
          <Newspaper className="w-8 h-8 mx-auto mb-2 text-slate-400" />
          <p className="text-sm font-medium text-slate-800">Couldn't load the news right now</p>
          <p className="text-xs text-slate-500 mt-1 max-w-md mx-auto">
            {error} — we've cached the latest headlines, so try again in a moment.
          </p>
          <button onClick={() => load(topic)} className="btn-secondary mt-4">
            Try Again
          </button>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item, i) => (
            <a
              key={i}
              href={item.link}
              target="_blank"
              rel="noopener noreferrer"
              className="card card-hover block group"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    {item.source && (
                      <span className="text-xs font-semibold text-transparent bg-instagram bg-clip-text">
                        {item.source}
                      </span>
                    )}
                    <span className="text-xs text-slate-500">{timeAgo(item.published_at)}</span>
                  </div>
                  <h3 className="text-sm font-medium text-slate-900 mt-1 group-hover:text-transparent group-hover:bg-instagram group-hover:bg-clip-text transition-colors">
                    {item.title}
                  </h3>
                  {item.summary && (
                    <p className="text-xs text-slate-500 mt-1.5 line-clamp-2">{item.summary}</p>
                  )}
                </div>
                <ExternalLink className="w-4 h-4 text-slate-500 shrink-0 mt-1 group-hover:text-pink-600 transition-colors" />
              </div>
            </a>
          ))}

          {items.length === 0 && (
            <div className="card text-center py-12 text-slate-500">
              <Newspaper className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>No news available right now</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
