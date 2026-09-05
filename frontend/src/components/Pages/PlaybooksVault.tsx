import React, { useState, useEffect } from 'react';
import { 
  BookOpen, 
  Search, 
  Plus, 
  Link as LinkIcon, 
  FileText, 
  CheckCircle2, 
  Zap, 
  Globe, 
  Bot, 
  Sparkles, 
  Code, 
  X, 
  Loader2, 
  ChevronDown, 
  ChevronUp
} from 'lucide-react';
import { apiService } from '../../services/api';
import { soundEngine } from '../../utils/soundEngine';

interface Playbook {
  id: string;
  category: string;
  tags: string[];
  trigger_signatures: string[];
  notes: string;
  exploit_template: string;
  source: string;
  confidence_score: number;
  times_used: number;
  success_rate: number;
  is_promoted: boolean;
}

export const PlaybooksVault: React.FC = () => {
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>('ALL');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Ingestion Modal State
  const [showIngestModal, setShowIngestModal] = useState<boolean>(false);
  const [ingestType, setIngestType] = useState<'url' | 'raw_text'>('url');
  const [ingestInput, setIngestInput] = useState<string>('');
  const [ingestCategory, setIngestCategory] = useState<string>('web');
  const [ingestTitle, setIngestTitle] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    fetchPlaybooks();
  }, []);

  const fetchPlaybooks = async () => {
    setLoading(true);
    try {
      const data = await apiService.getPlaybooks();
      if (data && data.playbooks) {
        setPlaybooks(data.playbooks);
      }
    } catch (err) {
      console.error('Failed to fetch playbooks:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (query: string) => {
    setSearchQuery(query);
    if (!query.trim()) {
      fetchPlaybooks();
      return;
    }
    try {
      const data = await apiService.searchPlaybooks(query, selectedCategory === 'ALL' ? undefined : selectedCategory);
      if (data && data.playbooks) {
        setPlaybooks(data.playbooks);
      }
    } catch (err) {
      console.error('Search failed:', err);
    }
  };

  const handleIngest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ingestInput.trim()) return;

    setIsSubmitting(true);
    setStatusMessage(null);
    soundEngine.playClick();

    try {
      const res = await apiService.ingestPlaybook({
        text: ingestInput,
        category: ingestCategory,
        source_type: ingestType,
        title: ingestTitle || undefined
      });

      if (res && res.status === 'INGESTED') {
        soundEngine.playSuccess();
        setStatusMessage({ type: 'success', text: `Playbook '${res.playbook_id}' successfully indexed into vault!` });
        setIngestInput('');
        setIngestTitle('');
        fetchPlaybooks();
        setTimeout(() => {
          setShowIngestModal(false);
          setStatusMessage(null);
        }, 1500);
      }
    } catch (err: any) {
      soundEngine.playAlarm();
      setStatusMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to ingest writeup. Check URL/text.' });
    } finally {
      setIsSubmitting(false);
    }
  };

  const filteredPlaybooks = playbooks.filter(pb => {
    if (selectedCategory !== 'ALL' && pb.category.toLowerCase() !== selectedCategory.toLowerCase()) {
      return false;
    }
    return true;
  });

  const getSourceBadge = (source: string) => {
    if (source === 'url') {
      return (
        <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] bg-sky-950/80 border border-sky-500/50 text-sky-400 font-bold uppercase">
          <Globe className="w-3 h-3" />
          <span>WEB URL</span>
        </span>
      );
    }
    if (source === 'generated' || source === 'auto_learned') {
      return (
        <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] bg-purple-950/80 border border-purple-500/50 text-purple-400 font-bold uppercase">
          <Bot className="w-3 h-3" />
          <span>AUTO-LEARNED</span>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] bg-emerald-950/80 border border-emerald-500/50 text-emerald-400 font-bold uppercase">
        <Sparkles className="w-3 h-3" />
        <span>HUMAN CURATED</span>
      </span>
    );
  };

  return (
    <div className="space-y-6 font-mono text-slate-100 pb-12">
      {/* Top Banner */}
      <div className="glass-panel border border-slate-800 p-6 rounded-2xl flex flex-col md:flex-row md:items-center md:justify-between gap-4 relative overflow-hidden bg-obsidian-900/60">
        <div className="absolute -right-10 -bottom-10 w-60 h-60 bg-cyber-cyan/5 rounded-full blur-3xl pointer-events-none"></div>

        <div className="space-y-1 z-10">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 rounded-xl bg-obsidian-800 border border-cyber-cyan/40 text-cyber-cyan shadow-[0_0_15px_rgba(0,240,255,0.2)]">
              <BookOpen className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-wider text-slate-100 font-display neon-text-cyan">
                KNOWLEDGE & PLAYBOOK VAULT
              </h1>
              <p className="text-xs text-slate-400">
                Teach FORGE by feeding writeups from Medium, Blogs, URLs, or Files for SQLite FTS5 real-time retrieval.
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-3 z-10">
          <button
            onClick={() => { soundEngine.playClick(); setShowIngestModal(true); }}
            className="px-5 py-2.5 rounded-xl bg-cyber-cyan text-obsidian-950 font-bold font-display text-xs uppercase flex items-center space-x-2 shadow-[0_0_20px_rgba(0,240,255,0.4)] hover:bg-cyan-300 transition-all hover:scale-105 active:scale-95"
          >
            <Plus className="w-4 h-4" />
            <span>FEEDS / INGEST WRITEUP</span>
          </button>
        </div>
      </div>

      {/* Filter & Search Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 glass-panel p-4 rounded-xl border border-slate-800 bg-obsidian-900/40">
        {/* Search Input */}
        <div className="relative flex-1">
          <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder="Search playbooks via FTS5 (e.g. 'jinja ssti', 'sqli bypass', 'pwntools')..."
            className="w-full bg-obsidian-950 border border-slate-800 rounded-lg pl-10 pr-4 py-2 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan/60 transition-all"
          />
        </div>

        {/* Category Filter Pills */}
        <div className="flex items-center space-x-1.5 overflow-x-auto pb-1 md:pb-0">
          {['ALL', 'WEB', 'PWN', 'REVERSE', 'CRYPTO', 'FORENSICS'].map((cat) => (
            <button
              key={cat}
              onClick={() => { soundEngine.playClick(); setSelectedCategory(cat); }}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all uppercase whitespace-nowrap ${
                selectedCategory === cat
                  ? 'bg-cyber-cyan/20 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_10px_rgba(0,240,255,0.2)]'
                  : 'text-slate-400 hover:text-slate-200 bg-obsidian-950 border border-slate-800'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Playbooks Grid */}
      {loading ? (
        <div className="flex flex-col items-center justify-center py-16 space-y-3">
          <Loader2 className="w-8 h-8 text-cyber-cyan animate-spin" />
          <p className="text-xs text-slate-400 tracking-wider">Querying Playbook Vault FTS5 index...</p>
        </div>
      ) : filteredPlaybooks.length === 0 ? (
        <div className="glass-panel border border-slate-800 p-12 rounded-xl text-center space-y-3">
          <BookOpen className="w-12 h-12 text-slate-600 mx-auto" />
          <p className="text-sm font-bold text-slate-300">No playbooks found</p>
          <p className="text-xs text-slate-400 max-w-md mx-auto">
            No matching playbooks in the vault. Feed writeups from Medium or web URLs to expand FORGE's exploit knowledge.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {filteredPlaybooks.map((pb) => {
            const isExpanded = expandedId === pb.id;

            return (
              <div
                key={pb.id}
                className="glass-panel border border-slate-800 hover:border-slate-700 rounded-xl p-5 transition-all bg-obsidian-900/40 space-y-4"
              >
                {/* Header */}
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-2.5 flex-wrap gap-y-1">
                      <span className="text-sm font-bold font-display text-cyber-cyan tracking-wide">
                        {pb.id}
                      </span>
                      {getSourceBadge(pb.source)}
                      <span className="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-300 font-bold uppercase border border-slate-700">
                        {pb.category}
                      </span>
                    </div>

                    <p className="text-xs text-slate-300 line-clamp-2">{pb.notes}</p>
                  </div>

                  {/* Right side Badges & Toggle */}
                  <div className="flex items-center space-x-3 shrink-0">
                    <div className="text-right font-mono">
                      <span className="text-[10px] text-slate-400 block uppercase">Confidence</span>
                      <span className="text-xs font-bold text-emerald-400">
                        {(pb.confidence_score * 100).toFixed(0)}%
                      </span>
                    </div>

                    <button
                      onClick={() => {
                        soundEngine.playClick();
                        setExpandedId(isExpanded ? null : pb.id);
                      }}
                      className="p-2 rounded-lg bg-obsidian-950 border border-slate-800 hover:border-cyber-cyan/50 text-slate-400 hover:text-cyber-cyan transition-colors"
                    >
                      {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {/* Trigger Signatures & Tags */}
                <div className="flex items-center space-x-2 flex-wrap gap-y-1.5 pt-1 border-t border-slate-800/80">
                  <span className="text-[10px] text-slate-400 font-bold uppercase flex items-center space-x-1 mr-1">
                    <Zap className="w-3 h-3 text-amber-400" />
                    <span>Triggers:</span>
                  </span>
                  {pb.trigger_signatures.map((sig, idx) => (
                    <span
                      key={idx}
                      className="px-2 py-0.5 rounded text-[10px] bg-amber-950/40 border border-amber-500/30 text-amber-300 font-mono"
                    >
                      {sig}
                    </span>
                  ))}

                  <div className="ml-auto flex items-center space-x-1.5">
                    {pb.tags.map((t, idx) => (
                      <span key={idx} className="text-[10px] text-slate-400 hover:text-slate-200">
                        #{t}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Expanded Exploit Template View */}
                {isExpanded && (
                  <div className="space-y-3 pt-3 border-t border-slate-800 animate-fadeIn">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-slate-200 flex items-center space-x-2">
                        <Code className="w-4 h-4 text-cyber-cyan" />
                        <span>Exploit Template & Action Sequence</span>
                      </span>
                      <span className="text-[10px] text-slate-400 font-mono">
                        Used {pb.times_used} time(s) · {pb.is_promoted ? 'Active Search Index' : 'Unpromoted'}
                      </span>
                    </div>

                    <pre className="p-4 rounded-lg bg-obsidian-950 border border-slate-800/80 text-xs text-cyan-300 font-mono overflow-x-auto max-h-80 leading-relaxed">
                      {pb.exploit_template}
                    </pre>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Writeup Ingestion Modal */}
      {showIngestModal && (
        <div className="fixed inset-0 z-50 bg-obsidian-950/80 backdrop-blur-md flex items-center justify-center p-4">
          <div className="glass-panel border border-cyber-cyan/40 bg-obsidian-900 w-full max-w-2xl rounded-2xl shadow-[0_0_30px_rgba(0,240,255,0.2)] p-6 space-y-5 animate-fadeIn">
            {/* Modal Header */}
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-3">
                <div className="p-2 rounded-lg bg-cyber-cyan/10 border border-cyber-cyan/40 text-cyber-cyan">
                  <Globe className="w-5 h-5" />
                </div>
                <div>
                  <h2 className="text-base font-bold font-display text-slate-100 neon-text-cyan">
                    INGEST CTF WRITEUP / URL
                  </h2>
                  <p className="text-xs text-slate-400">
                    Feed Medium articles, blog posts, or raw markdown writeups to teach FORGE.
                  </p>
                </div>
              </div>
              <button
                onClick={() => { soundEngine.playClick(); setShowIngestModal(false); }}
                className="p-1.5 rounded-lg border border-slate-800 text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleIngest} className="space-y-4">
              {/* Type Switcher */}
              <div className="flex items-center space-x-2 bg-obsidian-950 p-1 rounded-xl border border-slate-800">
                <button
                  type="button"
                  onClick={() => { soundEngine.playClick(); setIngestType('url'); }}
                  className={`flex-1 py-2 rounded-lg text-xs font-bold uppercase transition-all flex items-center justify-center space-x-2 ${
                    ingestType === 'url'
                      ? 'bg-cyber-cyan text-obsidian-950 shadow-[0_0_10px_rgba(0,240,255,0.4)]'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Globe className="w-4 h-4" />
                  <span>Web URL (Medium / Blog)</span>
                </button>

                <button
                  type="button"
                  onClick={() => { soundEngine.playClick(); setIngestType('raw_text'); }}
                  className={`flex-1 py-2 rounded-lg text-xs font-bold uppercase transition-all flex items-center justify-center space-x-2 ${
                    ingestType === 'raw_text'
                      ? 'bg-cyber-cyan text-obsidian-950 shadow-[0_0_10px_rgba(0,240,255,0.4)]'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <FileText className="w-4 h-4" />
                  <span>Raw Text / Markdown</span>
                </button>
              </div>

              {/* Title & Category Row */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] font-bold text-slate-400 uppercase block mb-1">
                    Playbook Category
                  </label>
                  <select
                    value={ingestCategory}
                    onChange={(e) => setIngestCategory(e.target.value)}
                    className="w-full bg-obsidian-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan/60"
                  >
                    <option value="web">WEB</option>
                    <option value="pwn">PWN</option>
                    <option value="reverse">REVERSE</option>
                    <option value="crypto">CRYPTO</option>
                    <option value="forensics">FORENSICS</option>
                    <option value="osint">OSINT</option>
                  </select>
                </div>

                <div>
                  <label className="text-[10px] font-bold text-slate-400 uppercase block mb-1">
                    Custom Title (Optional)
                  </label>
                  <input
                    type="text"
                    value={ingestTitle}
                    onChange={(e) => setIngestTitle(e.target.value)}
                    placeholder="e.g. Medium Jinja2 SSTI Filter Bypass"
                    className="w-full bg-obsidian-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan/60"
                  />
                </div>
              </div>

              {/* Main Input Area */}
              <div>
                <label className="text-[10px] font-bold text-slate-400 uppercase block mb-1">
                  {ingestType === 'url' ? 'Writeup URL (Medium, GitHub, HackMD, Blog)' : 'Writeup Markdown Content'}
                </label>
                {ingestType === 'url' ? (
                  <div className="relative">
                    <LinkIcon className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                    <input
                      type="url"
                      required
                      value={ingestInput}
                      onChange={(e) => setIngestInput(e.target.value)}
                      placeholder="https://medium.com/@author/ctf-writeup-123"
                      className="w-full bg-obsidian-950 border border-slate-800 rounded-lg pl-10 pr-3 py-2.5 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan/60 font-mono"
                    />
                  </div>
                ) : (
                  <textarea
                    required
                    rows={6}
                    value={ingestInput}
                    onChange={(e) => setIngestInput(e.target.value)}
                    placeholder="Paste markdown writeup text here..."
                    className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-3 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan/60 font-mono"
                  ></textarea>
                )}
              </div>

              {/* Status Notifications */}
              {statusMessage && (
                <div
                  className={`p-3 rounded-lg text-xs font-bold border flex items-center space-x-2 ${
                    statusMessage.type === 'success'
                      ? 'bg-emerald-950/80 border-emerald-500/60 text-emerald-300'
                      : 'bg-rose-950/80 border-rose-500/60 text-rose-300'
                  }`}
                >
                  <CheckCircle2 className="w-4 h-4 shrink-0" />
                  <span>{statusMessage.text}</span>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex items-center justify-end space-x-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowIngestModal(false)}
                  className="px-4 py-2 rounded-lg text-xs font-bold text-slate-400 hover:text-slate-200 bg-slate-800/60 transition-colors uppercase"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-5 py-2 rounded-lg bg-cyber-cyan text-obsidian-950 font-bold font-display text-xs uppercase flex items-center space-x-2 shadow-[0_0_15px_rgba(0,240,255,0.4)] hover:bg-cyan-300 transition-all disabled:opacity-50"
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Ingesting & Indexing...</span>
                    </>
                  ) : (
                    <>
                      <Sparkles className="w-4 h-4" />
                      <span>Ingest into Vault</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
