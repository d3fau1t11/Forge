import React, { useState, useEffect } from 'react';
import {
  BrainCircuit,
  Search,
  CheckCircle2,
  XCircle,
  ShieldAlert,
  Loader2,
  ChevronDown,
  ChevronUp,
  Award,
  Clock,
  Link as LinkIcon,
  ThumbsUp,
  ThumbsDown,
  Zap,
  FlaskConical
} from 'lucide-react';
import { apiService } from '../../services/api';
import { soundEngine } from '../../utils/soundEngine';

interface Brief {
  id: string;
  technique: string;
  category: string;
  outcome: string;
  confidence: number;
  success_rate: number;
  times_used: number;
  times_successful: number;
  created_at?: string | null;
  last_used?: string | null;
}

interface Experience {
  id: string;
  technique: string;
  category: string;
  difficulty: string;
  outcome: string;
  confidence: number;
  success_rate: number;
  times_retrieved: number;
  times_used: number;
  times_successful: number;
  times_failed: number;
  tags: string[];
  applicable_conditions: string;
  generalized_strategy: string;
  observed_conditions: string;
  failed_techniques: Array<{ approach: string; reason: string }>;
  successful_attack_chain: string[];
  success_indicators: string[];
  technologies: string[];
  prerequisites: string[];
  detection_indicators: Record<string, string[]>;
  verification_evidence: string;
  source: string;
  source_run_id?: string | null;
  source_challenge_id?: string | null;
  challenge_name?: string;
  promoted_playbook_id?: string | null;
  created_at?: string | null;
  last_used?: string | null;
  attempts?: Array<{ sequence: number; approach: string; outcome: string; reason: string; evidence: string }>;
}

interface MemoryStats {
  total_memories: number;
  successful_experiences: number;
  failed_experiences: number;
  high_confidence_techniques: number;
  promoted_playbooks: number;
  recent_techniques: Brief[];
  most_successful_techniques: Brief[];
  recently_used_memories: Brief[];
}

const fmtDate = (iso?: string | null): string => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

export const ExperienceMemory: React.FC = () => {
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [experiences, setExperiences] = useState<Experience[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>('ALL');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Experience | null>(null);

  useEffect(() => {
    fetchMemory();
  }, []);

  const fetchMemory = async () => {
    setLoading(true);
    try {
      const data = await apiService.getMemory(selectedCategory);
      setStats(data.stats || null);
      setExperiences(data.experiences || []);
    } catch (err) {
      console.error('Failed to fetch memory:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (query: string) => {
    setSearchQuery(query);
    if (!query.trim()) {
      fetchMemory();
      return;
    }
    try {
      const data = await apiService.searchMemory(query, selectedCategory);
      // Map unified retrieval results back to experiences we can render.
      const expOnly = (data.memories || []).filter((m: any) => m.kind === 'experience');
      const ids = new Set(expOnly.map((m: any) => m.id));
      const merged = experiences.filter(e => ids.has(e.id));
      // Fall back to showing the raw retrieval technique labels if not in the list yet.
      if (merged.length) {
        setExperiences(merged);
      } else {
        await fetchMemory();
      }
    } catch (err) {
      console.error('Memory search failed:', err);
    }
  };

  const toggleExpand = async (id: string) => {
    soundEngine.playClick();
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    setDetail(null);
    try {
      const d = await apiService.getMemoryDetail(id);
      setDetail(d);
    } catch (err) {
      console.error('Failed to load experience detail:', err);
    }
  };

  const submitFeedback = async (id: string, success: boolean) => {
    try {
      success ? soundEngine.playSuccess() : soundEngine.playClick();
      await apiService.sendMemoryFeedback(id, success, 'operator feedback');
      await fetchMemory();
      if (expandedId === id) {
        const d = await apiService.getMemoryDetail(id);
        setDetail(d);
      }
    } catch (err) {
      console.error('Feedback failed:', err);
    }
  };

  const categories = ['ALL', 'web', 'pwn', 'crypto', 'reverse', 'forensics', 'osint'];

  const filtered = experiences.filter(e =>
    selectedCategory === 'ALL' || (e.category || '').toLowerCase() === selectedCategory.toLowerCase()
  );

  const outcomeBadge = (outcome: string) => (
    outcome === 'success'
      ? <span className="inline-flex items-center space-x-1 text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-950/60 border border-cyber-emerald/50 text-cyber-emerald"><CheckCircle2 className="w-3 h-3" /><span>SUCCESS</span></span>
      : <span className="inline-flex items-center space-x-1 text-[10px] font-bold px-2 py-0.5 rounded bg-rose-950/60 border border-cyber-rose/50 text-cyber-rose"><XCircle className="w-3 h-3" /><span>FAILURE</span></span>
  );

  return (
    <div className="space-y-6 font-mono text-slate-100 pb-10">
      {/* Header */}
      <div className="glass-panel border border-slate-800 p-4 rounded-xl flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-lg bg-obsidian-900 border border-cyber-violet/60 flex items-center justify-center shadow-[0_0_15px_rgba(157,78,221,0.3)]">
            <BrainCircuit className="w-5 h-5 text-cyber-violet" />
          </div>
          <div>
            <h1 className="font-display font-bold tracking-widest text-lg">FORGE MEMORY</h1>
            <p className="text-[10px] text-slate-400 uppercase tracking-widest">Experience learned from real solves — SOLVE → LEARN → REMEMBER → SOLVE FASTER</p>
          </div>
        </div>
        <span className="text-[10px] text-cyber-violet font-bold px-3 py-1 rounded-lg bg-obsidian-950 border border-cyber-violet/40">
          GENERALIZED · NO CHALLENGE SECRETS STORED
        </span>
      </div>

      {/* Stats tiles */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatTile label="Total Memories" value={stats.total_memories} icon={<BrainCircuit className="w-4 h-4" />} color="text-cyber-cyan" />
          <StatTile label="Successful" value={stats.successful_experiences} icon={<CheckCircle2 className="w-4 h-4" />} color="text-cyber-emerald" />
          <StatTile label="Failed (learned)" value={stats.failed_experiences} icon={<XCircle className="w-4 h-4" />} color="text-cyber-rose" />
          <StatTile label="High-Confidence" value={stats.high_confidence_techniques} icon={<Award className="w-4 h-4" />} color="text-cyber-amber" />
          <StatTile label="Promoted → Playbook" value={stats.promoted_playbooks} icon={<FlaskConical className="w-4 h-4" />} color="text-cyber-violet" />
        </div>
      )}

      {/* Leaderboards */}
      {stats && stats.total_memories > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Leaderboard title="Recently Learned" icon={<Zap className="w-3.5 h-3.5" />} items={stats.recent_techniques} showDate="created_at" />
          <Leaderboard title="Most Successful" icon={<Award className="w-3.5 h-3.5" />} items={stats.most_successful_techniques} metric="times_successful" />
          <Leaderboard title="Recently Used" icon={<Clock className="w-3.5 h-3.5" />} items={stats.recently_used_memories} showDate="last_used" />
        </div>
      )}

      {/* Search + filters */}
      <div className="glass-panel border border-slate-800 p-3 rounded-xl flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder="Search learned techniques (e.g. ssti, upload bypass, jwt)…"
            className="w-full bg-obsidian-950 border border-slate-800 rounded-lg pl-9 pr-3 py-2 text-xs focus:border-cyber-cyan/60 focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => { setSelectedCategory(cat); soundEngine.playTabSwitch(); }}
              className={`px-2.5 py-1 rounded text-[10px] font-bold uppercase transition-all ${
                selectedCategory === cat
                  ? 'bg-cyber-cyan text-obsidian-950'
                  : 'text-slate-400 hover:text-slate-200 bg-obsidian-900/60 border border-slate-800'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Experience list */}
      {loading ? (
        <div className="flex items-center justify-center py-16 text-slate-500">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading memory…
        </div>
      ) : filtered.length === 0 ? (
        <div className="glass-panel border border-slate-800 rounded-xl py-16 text-center">
          <BrainCircuit className="w-10 h-10 text-slate-700 mx-auto mb-3" />
          <p className="text-slate-400 font-bold tracking-widest">NO FORGE EXPERIENCE YET</p>
          <p className="text-[11px] text-slate-600 mt-1">Experiences are learned automatically the moment FORGE verifies a flag.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map(exp => (
            <div key={exp.id} className="glass-panel border border-slate-800 rounded-xl overflow-hidden">
              <button
                onClick={() => toggleExpand(exp.id)}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-900/40 transition-colors text-left"
              >
                <div className="flex items-center space-x-3 min-w-0">
                  {outcomeBadge(exp.outcome)}
                  <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-obsidian-950 border border-slate-700 text-slate-300 uppercase">{exp.category}</span>
                  <span className="font-bold text-sm text-slate-100 truncate">{exp.technique}</span>
                  {exp.promoted_playbook_id && (
                    <span title="Promoted to Playbook Vault" className="text-cyber-violet"><FlaskConical className="w-3.5 h-3.5" /></span>
                  )}
                </div>
                <div className="flex items-center space-x-3 shrink-0">
                  <span className="text-[10px] text-slate-400">conf <span className="text-cyber-cyan font-bold">{(exp.confidence * 100).toFixed(0)}%</span></span>
                  <span className="text-[10px] text-slate-400">success <span className="text-cyber-emerald font-bold">{(exp.success_rate * 100).toFixed(0)}%</span></span>
                  <span className="text-[10px] text-slate-400">used <span className="text-slate-200 font-bold">{exp.times_used}</span></span>
                  {expandedId === exp.id ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
                </div>
              </button>

              {expandedId === exp.id && (
                <div className="border-t border-slate-800 px-4 py-4 space-y-3 text-xs bg-obsidian-950/40">
                  {!detail ? (
                    <div className="flex items-center text-slate-500"><Loader2 className="w-4 h-4 animate-spin mr-2" /> Loading detail…</div>
                  ) : (
                    <>
                      <Field label="Applicable Conditions" value={detail.applicable_conditions} />
                      <Field label="Generalized Strategy" value={detail.generalized_strategy} />
                      {detail.tags?.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {detail.tags.map(t => <span key={t} className="text-[9px] px-1.5 py-0.5 rounded bg-cyber-cyan/10 border border-cyber-cyan/30 text-cyber-cyan">{t}</span>)}
                        </div>
                      )}
                      {detail.successful_attack_chain?.length > 0 && (
                        <ListField label="Successful Attack Chain (generalized)" items={detail.successful_attack_chain} mono accent="text-cyber-emerald" />
                      )}
                      {detail.failed_techniques?.length > 0 && (
                        <div>
                          <p className="text-[10px] font-bold text-cyber-rose uppercase tracking-wider mb-1">Failed Approaches (don't blindly repeat)</p>
                          <div className="space-y-1">
                            {detail.failed_techniques.map((f, i) => (
                              <div key={i} className="bg-rose-950/20 border border-cyber-rose/20 rounded px-2 py-1">
                                <span className="text-slate-300 font-mono">{f.approach}</span>
                                <span className="text-slate-500"> — {f.reason}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {detail.success_indicators?.length > 0 && (
                        <ListField label="Success Indicators" items={detail.success_indicators} accent="text-cyber-emerald" />
                      )}
                      {detail.detection_indicators && Object.keys(detail.detection_indicators).length > 0 && (
                        <div className="border border-cyber-amber/20 rounded-lg p-2 bg-amber-950/10">
                          <p className="text-[10px] font-bold text-cyber-amber uppercase tracking-wider mb-1 flex items-center"><ShieldAlert className="w-3.5 h-3.5 mr-1" />Blue-Team Knowledge (derived)</p>
                          {Object.entries(detail.detection_indicators).map(([k, v]) => (
                            Array.isArray(v) && v.length > 0 ? (
                              <div key={k} className="mb-1">
                                <span className="text-[10px] text-slate-400 uppercase">{k}: </span>
                                <span className="text-slate-300">{v.join('; ')}</span>
                              </div>
                            ) : null
                          ))}
                        </div>
                      )}

                      {/* Provenance + stats */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pt-2 border-t border-slate-800 text-[10px]">
                        <Meta label="Source" value={detail.source} />
                        <Meta label="Source Run" value={detail.source_run_id || '—'} mono />
                        <Meta label="Source Challenge" value={detail.challenge_name || detail.source_challenge_id || '—'} />
                        <Meta label="Difficulty" value={detail.difficulty} />
                        <Meta label="Retrieved" value={String(detail.times_retrieved)} />
                        <Meta label="Used / Success / Fail" value={`${detail.times_used} / ${detail.times_successful} / ${detail.times_failed}`} />
                        <Meta label="Created" value={fmtDate(detail.created_at)} />
                        <Meta label="Last Used" value={fmtDate(detail.last_used)} />
                      </div>

                      {detail.promoted_playbook_id && (
                        <div className="flex items-center text-[10px] text-cyber-violet">
                          <LinkIcon className="w-3 h-3 mr-1" /> Promoted to Playbook Vault entry: <span className="font-mono ml-1">{detail.promoted_playbook_id}</span>
                        </div>
                      )}

                      {/* Feedback */}
                      <div className="flex items-center space-x-2 pt-2 border-t border-slate-800">
                        <span className="text-[10px] text-slate-400 uppercase tracking-wider">Was this memory useful?</span>
                        <button onClick={() => submitFeedback(exp.id, true)} className="flex items-center space-x-1 px-2 py-1 rounded text-[10px] font-bold bg-emerald-950/40 border border-cyber-emerald/40 text-cyber-emerald hover:bg-emerald-900/40">
                          <ThumbsUp className="w-3 h-3" /><span>Helped</span>
                        </button>
                        <button onClick={() => submitFeedback(exp.id, false)} className="flex items-center space-x-1 px-2 py-1 rounded text-[10px] font-bold bg-rose-950/40 border border-cyber-rose/40 text-cyber-rose hover:bg-rose-900/40">
                          <ThumbsDown className="w-3 h-3" /><span>Didn't apply</span>
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const StatTile: React.FC<{ label: string; value: number; icon: React.ReactNode; color: string }> = ({ label, value, icon, color }) => (
  <div className="glass-panel border border-slate-800 rounded-xl p-3">
    <div className={`flex items-center space-x-1.5 ${color} mb-1`}>{icon}<span className="text-[9px] uppercase tracking-widest text-slate-400">{label}</span></div>
    <div className={`text-2xl font-display font-bold ${color}`}>{value}</div>
  </div>
);

const Leaderboard: React.FC<{ title: string; icon: React.ReactNode; items: Brief[]; metric?: string; showDate?: string }> = ({ title, icon, items, metric, showDate }) => (
  <div className="glass-panel border border-slate-800 rounded-xl p-3">
    <div className="flex items-center space-x-1.5 text-slate-300 mb-2">{icon}<span className="text-[10px] font-bold uppercase tracking-widest">{title}</span></div>
    {items.length === 0 ? (
      <p className="text-[10px] text-slate-600">Nothing yet.</p>
    ) : (
      <div className="space-y-1">
        {items.slice(0, 5).map(it => (
          <div key={it.id} className="flex items-center justify-between text-[10px]">
            <span className="text-slate-300 truncate mr-2">{it.technique}</span>
            <span className="text-slate-500 shrink-0">
              {metric === 'times_successful' ? `${it.times_successful}×` :
                showDate === 'created_at' ? fmtDate(it.created_at).split(',')[0] :
                showDate === 'last_used' ? fmtDate(it.last_used).split(',')[0] : ''}
            </span>
          </div>
        ))}
      </div>
    )}
  </div>
);

const Field: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  value ? (
    <div>
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">{label}</p>
      <p className="text-slate-200 leading-relaxed">{value}</p>
    </div>
  ) : null
);

const ListField: React.FC<{ label: string; items: string[]; mono?: boolean; accent?: string }> = ({ label, items, mono, accent }) => (
  <div>
    <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">{label}</p>
    <div className="space-y-0.5">
      {items.map((it, i) => (
        <div key={i} className={`${mono ? 'font-mono' : ''} ${accent || 'text-slate-300'} bg-obsidian-950/60 border border-slate-800 rounded px-2 py-1`}>{it}</div>
      ))}
    </div>
  </div>
);

const Meta: React.FC<{ label: string; value: string; mono?: boolean }> = ({ label, value, mono }) => (
  <div>
    <span className="text-slate-500 uppercase">{label}</span>
    <div className={`${mono ? 'font-mono' : ''} text-slate-300 truncate`} title={value}>{value}</div>
  </div>
);
