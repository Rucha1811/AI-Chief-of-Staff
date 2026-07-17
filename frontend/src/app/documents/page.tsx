"use client";

import { useEffect, useState, useRef } from "react";
import { api, Document } from "@/lib/api";
import { FileText, Upload, Sparkles, Trash2, Search, Send, File, Loader2 } from "lucide-react";

export default function DocumentsPage() {
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [selectedDoc, setSelectedDoc] = useState<Document | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [answering, setAnswering] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    setLoading(true);
    const d = await api.documents.list();
    setDocs(d);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const uploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await api.documents.upload(file);
      await load();
    } finally {
      setUploading(false);
    }
  };

  const deleteDoc = async (id: number) => {
    await api.documents.delete(id);
    if (selectedDoc?.id === id) setSelectedDoc(null);
    await load();
  };

  const summarize = async (id: number) => {
    await api.documents.summarize(id);
    await load();
    const doc = docs.find((d) => d.id === id);
    if (doc) setSelectedDoc({ ...doc, summary: doc.summary, key_points: doc.key_points, deadlines: doc.deadlines });
  };

  const askQuestion = async () => {
    if (!selectedDoc || !question.trim()) return;
    setAnswering(true);
    setAnswer(null);
    try {
      const result = await api.documents.query(selectedDoc.id, question);
      setAnswer(result.answer);
    } finally {
      setAnswering(false);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Document Intelligence</h1>
          <p className="text-slate-400 mt-1">{docs.length} documents uploaded</p>
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.pptx,.txt,.png,.jpg,.jpeg"
            onChange={uploadFile}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="btn-primary flex items-center gap-1.5"
          >
            {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {uploading ? "Uploading..." : "Upload Document"}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500" />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1 space-y-2">
            <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Documents</h3>
            {docs.map((doc) => (
              <button
                key={doc.id}
                onClick={() => { setSelectedDoc(doc); setAnswer(null); setQuestion(""); }}
                className={`w-full card text-left flex items-center gap-3 hover:bg-slate-800/70 transition-colors ${
                  selectedDoc?.id === doc.id ? "ring-1 ring-indigo-600" : ""
                }`}
              >
                <FileText className="w-8 h-8 text-indigo-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200 truncate">{doc.filename}</p>
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <span>{doc.file_type}</span>
                    <span>{formatSize(doc.file_size)}</span>
                    {doc.is_indexed && <Sparkles className="w-3 h-3 text-indigo-400" />}
                  </div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); deleteDoc(doc.id); }} className="btn-ghost p-1 text-slate-500 hover:text-red-400">
                  <Trash2 className="w-4 h-4" />
                </button>
              </button>
            ))}
            {docs.length === 0 && (
              <p className="text-sm text-slate-500 text-center py-8">Upload a document to get started</p>
            )}
          </div>

          <div className="lg:col-span-2 space-y-4">
            {selectedDoc ? (
              <>
                <div className="card">
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h2 className="text-lg font-semibold text-slate-100">{selectedDoc.filename}</h2>
                      <p className="text-xs text-slate-500">
                        {selectedDoc.file_type} · {formatSize(selectedDoc.file_size)}
                      </p>
                    </div>
                    <button onClick={() => summarize(selectedDoc.id)} className="btn-secondary flex items-center gap-1.5 text-sm">
                      <Sparkles className="w-4 h-4" /> AI Summarize
                    </button>
                  </div>

                  {selectedDoc.summary && (
                    <div className="mb-4">
                      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Summary</h3>
                      <p className="text-sm text-slate-300">{selectedDoc.summary}</p>
                    </div>
                  )}

                  {selectedDoc.key_points && (
                    <div className="mb-4">
                      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Key Points</h3>
                      <ul className="text-sm text-slate-300 space-y-1">
                        {selectedDoc.key_points.split("\n").map((pt, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <span className="text-indigo-400 mt-0.5">•</span>
                            {pt}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {selectedDoc.deadlines && (
                    <div className="mb-4 bg-red-500/10 p-3 rounded-lg">
                      <h3 className="text-xs font-semibold text-red-400 uppercase tracking-wider mb-1">Deadlines</h3>
                      <p className="text-sm text-red-300">{selectedDoc.deadlines}</p>
                    </div>
                  )}
                </div>

                <div className="card space-y-3">
                  <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                    <Search className="w-4 h-4" /> Ask about this document
                  </h3>
                  <div className="flex gap-2">
                    <input
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && askQuestion()}
                      placeholder="What is this document about?"
                      className="input flex-1"
                    />
                    <button onClick={askQuestion} disabled={answering || !question.trim()} className="btn-primary">
                      {answering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                    </button>
                  </div>

                  {answering && (
                    <div className="flex items-center gap-2 text-sm text-slate-400">
                      <Loader2 className="w-4 h-4 animate-spin" /> Analyzing document...
                    </div>
                  )}

                  {answer && (
                    <div className="bg-slate-900/50 p-3 rounded-lg">
                      <p className="text-xs text-slate-500 mb-1">Q: {question}</p>
                      <p className="text-sm text-slate-300">{answer}</p>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="card flex flex-col items-center justify-center py-16 text-slate-500">
                <File className="w-12 h-12 mb-3 opacity-30" />
                <p>Select a document to view details</p>
                <p className="text-xs mt-1">Upload PDF, DOCX, PPTX, TXT, or images</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
