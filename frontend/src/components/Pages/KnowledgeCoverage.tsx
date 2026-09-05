import React, { useState, useEffect, useMemo } from 'react';
import {
  Brain,
  Globe,
  Crosshair,
  Cpu,
  Lock,
  Search,
  Eye,
  Wifi,
  Smartphone,
  Cloud,
  HardDrive,
  Bot,
  Layers,
  ChevronDown,
  ChevronUp,
  Sparkles,
  ShieldCheck,
  AlertTriangle,
  Clock,
  TrendingUp,
  Database,
  Zap,
  RefreshCw
} from 'lucide-react';
import { apiService } from '../../services/api';

interface TagInfo {
  tag: string;
  count: number;
}

interface CategoryCoverage {
  category: string;
  total: number;
  by_source: Record<string, number>;
  by_confidence_tier: { pending: number; low: number; trusted: number };
  distinct_tags: TagInfo[];
}

interface CoverageData {
  grand_total: number;
  categories: CategoryCoverage[];
}

interface KnowledgeCoverageProps {
  refreshTrigger?: number;
}

const CATEGORY_META: Record<string, { label: string; icon: React.ReactNode; gradient: string }> = {
  web: { label: 'Web', icon: <Globe className="w-5 h-5" />, gradient: 'from-cyan-500/20 to-blue-600/10' },
  pwn: { label: 'Pwn', icon: <Crosshair className="w-5 h-5" />, gradient: 'from-red-500/20 to-orange-600/10' },
  reverse: { label: 'Reverse', icon: <Cpu className="w-5 h-5" />, gradient: 'from-purple-500/20 to-violet-600/10' },
  crypto: { label: 'Crypto', icon: <Lock className="w-5 h-5" />, gradient: 'from-amber-500/20 to-yellow-600/10' },
  forensics: { label: 'Forensics', icon: <Search className="w-5 h-5" />, gradient: 'from-emerald-500/20 to-green-600/10' },
  osint: { label: 'OSINT', icon: <Eye className="w-5 h-5" />, gradient: 'from-sky-500/20 to-indigo-600/10' },
  network: { label: 'Network', icon: <Wifi className="w-5 h-5" />, gradient: 'from-teal-500/20 to-cyan-600/10' },
  mobile: { label: 'Mobile', icon: <Smartphone className="w-5 h-5" />, gradient: 'from-pink-500/20 to-rose-600/10' },
  cloud: { label: 'Cloud', icon: <Cloud className="w-5 h-5" />, gradient: 'from-blue-500/20 to-sky-600/10' },
  hardware: { label: 'Hardware', icon: <HardDrive className="w-5 h-5" />, gradient: 'from-stone-500/20 to-slate-600/10' },
  ai_llm: { label: 'AI / LLM', icon: <Bot className="w-5 h-5" />, gradient: 'from-violet-500/20 to-fuchsia-600/10' },
  misc: { label: 'Misc', icon: <Layers className="w-5 h-5" />, gradient: 'from-slate-500/20 to-gray-600/10' },
};

function getStatusColor(count: number): { ring: string; glow: string; label: string; bg: string } {
  if (count <= 1) return { ring: 'stroke-red-500', glow: 'shadow-[0_0_20px_rgba(239,68,68,0.3)]', label: 'CRITICAL GAP', bg: 'bg-red-500/10 border-red-500/30' };
  if (count <= 5) return { ring: 'stroke-amber-400', glow: 'shadow-[0_0_20px_rgba(251,191,36,0.25)]', label: 'LOW COVERAGE', bg: 'bg-amber-500/10 border-amber-500/30' };
  return { ring: 'stroke-emerald-400', glow: 'shadow-[0_0_20px_rgba(52,211,153,0.25)]', label: 'OPERATIONAL', bg: 'bg-emerald-500/10 border-emerald-500/30' };
}

function RadialProgress({ value, max, statusColor }: { value: number; max: number; statusColor: string }) {
  const radius = 28;
  const circumference = 2 * Math.PI * radius;
  const pct = max > 0 ? Math.min(value / max, 1) : 0;
  const offset = circumference * (1 - pct);

  return (
    <svg width="72" height="72" viewBox="0 0 72 72" className="transform -rotate-90">
      <circle cx="36" cy="36" r={radius} fill="none" stroke="rgba(100,116,139,0.15)" strokeWidth="5" />
      <circle
        cx="36" cy="36" r={radius} fill="none"
        className={`${statusColor} transition-all duration-1000 ease-out`}
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
      />
    </svg>
  );
}

function ConfidenceBar({ tier }: { tier: { pending: number; low: number; trusted: number } }) {
  const total = tier.pending + tier.low + tier.trusted;
  if (total === 0) return null;

  const pctPending = (tier.pending / total) * 100;
  const pctLow = (tier.low / total) * 100;
  const pctTrusted = (tier.trusted / total) * 100;

  return (
    <div className="mt-3 space-y-1.5">
      <div className="flex items-center justify-between text-[9px] font-mono text-slate-400 uppercase tracking-wider">
        <span>Confidence Distribution</span>
        <span>{total} total</span>
      </div>
      <div className="h-2 w-full rounded-full bg-slate-800/80 overflow-hidden flex">
        {pctTrusted > 0 && (
          <div
            className="h-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-700"
            style={{ width: `${pctTrusted}%` }}
            title={`Trusted: ${tier.trusted}`}
          />
        )}
        {pctLow > 0 && (
          <div
            className="h-full bg-gradient-to-r from-amber-500 to-amber-400 transition-all duration-700"
            style={{ width: `${pctLow}%` }}
            title={`Low: ${tier.low}`}
          />
        )}
        {pctPending > 0 && (
          <div
            className="h-full bg-gradient-to-r from-red-500 to-red-400 transition-all duration-700"
            style={{ width: `${pctPending}%` }}
            title={`Pending: ${tier.pending}`}
          />
        )}
      </div>
      <div className="flex items-center space-x-3 text-[9px] font-mono">
        <span className="flex items-center space-x-1">
          <span className="w-2 h-2 rounded-full bg-emerald-400" />
          <span className="text-emerald-400">{tier.trusted} trusted</span>
        </span>
        <span className="flex items-center space-x-1">
          <span className="w-2 h-2 rounded-full bg-amber-400" />
          <span className="text-amber-400">{tier.low} low</span>
        </span>
        <span className="flex items-center space-x-1">
          <span className="w-2 h-2 rounded-full bg-red-400" />
          <span className="text-red-400">{tier.pending} pending</span>
        </span>
      </div>
    </div>
  );
}

export const KnowledgeCoverage: React.FC<KnowledgeCoverageProps> = ({ refreshTrigger = 0 }) => {
  const [data, setData] = useState<CoverageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string>('');

  const fetchCoverage = async () => {
    setLoading(true);
    try {
      const result = await apiService.getKnowledgeCoverage();
      setData(result);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (e) {
      console.error('Failed to fetch coverage data:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCoverage();
  }, [refreshTrigger]);

  const maxCount = useMemo(() => {
    if (!data) return 1;
    return Math.max(...data.categories.map(c => c.total), 1);
  }, [data]);

  // Grand stats
  const grandStats = useMemo(() => {
    if (!data) return { total: 0, ingested: 0, generated: 0, trustedPct: 0 };
    let ingested = 0, generated = 0, trusted = 0, total = 0;
    for (const cat of data.categories) {
      total += cat.total;
      for (const [src, count] of Object.entries(cat.by_source)) {
        if (src === 'generated') generated += count;
        else ingested += count;
      }
      trusted += cat.by_confidence_tier.trusted;
    }
    return { total, ingested, generated, trustedPct: total > 0 ? Math.round((trusted / total) * 100) : 0 };
  }, [data]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center space-y-3">
          <Brain className="w-10 h-10 text-cyan-400 animate-pulse mx-auto" />
          <p className="text-xs text-slate-400 font-mono tracking-wider">SCANNING KNOWLEDGE MATRIX...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5 animate-fadeIn">
      {/* HEADER */}
      <div className="glass-panel cyber-corner rounded-xl p-5">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center space-x-4">
            <div className="relative flex items-center justify-center w-12 h-12 rounded-xl bg-gradient-to-br from-cyan-500/20 to-violet-600/20 border border-cyan-500/40 shadow-[0_0_25px_rgba(0,240,255,0.2)]">
              <Brain className="w-6 h-6 text-cyan-400" />
              <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-emerald-400 animate-pulse border-2 border-[#06090e]" />
            </div>
            <div>
              <h1 className="font-display text-xl font-bold tracking-wider neon-text-cyan flex items-center space-x-2">
                <span>KNOWLEDGE COVERAGE MATRIX</span>
                <Sparkles className="w-4 h-4 text-amber-400 animate-pulse" />
              </h1>
              <p className="text-[10px] text-slate-400 font-mono tracking-widest mt-0.5">
                REAL-TIME EXPLOIT KNOWLEDGE OBSERVABILITY &bull; {data?.categories.length || 0} CATEGORIES TRACKED
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2 flex-wrap gap-2">
            {/* Grand Stats Badges */}
            <div className="px-3 py-1.5 rounded-lg bg-cyan-500/10 border border-cyan-500/30 text-center">
              <div className="text-lg font-bold text-cyan-400 font-mono">{grandStats.total.toLocaleString()}</div>
              <div className="text-[8px] text-slate-400 uppercase tracking-wider">Total</div>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-center">
              <div className="text-lg font-bold text-emerald-400 font-mono">{grandStats.ingested.toLocaleString()}</div>
              <div className="text-[8px] text-slate-400 uppercase tracking-wider">Ingested</div>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-violet-500/10 border border-violet-500/30 text-center">
              <div className="text-lg font-bold text-violet-400 font-mono">{grandStats.generated}</div>
              <div className="text-[8px] text-slate-400 uppercase tracking-wider">Generated</div>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-center">
              <div className="text-lg font-bold text-amber-400 font-mono">{grandStats.trustedPct}%</div>
              <div className="text-[8px] text-slate-400 uppercase tracking-wider">Trusted</div>
            </div>

            <button
              onClick={fetchCoverage}
              className="p-2.5 rounded-lg border border-slate-700 hover:border-cyan-500/50 bg-slate-900/60 text-slate-400 hover:text-cyan-400 transition-all group"
              title="Refresh Coverage Data"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : 'group-hover:rotate-180 transition-transform duration-500'}`} />
            </button>
          </div>
        </div>

        {/* Last Updated */}
        {lastUpdated && (
          <div className="mt-3 flex items-center space-x-2 text-[9px] text-slate-500 font-mono">
            <Clock className="w-3 h-3" />
            <span>Last scan: {lastUpdated}</span>
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-emerald-400">LIVE</span>
          </div>
        )}
      </div>

      {/* CATEGORY GRID */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {data?.categories.map((cat) => {
          const meta = CATEGORY_META[cat.category] || CATEGORY_META.misc;
          const status = getStatusColor(cat.total);
          const isExpanded = expandedCategory === cat.category;
          const sourceBadges = Object.entries(cat.by_source);

          return (
            <div
              key={cat.category}
              className={`glass-panel rounded-xl overflow-hidden transition-all duration-300 cursor-pointer border ${
                isExpanded
                  ? 'border-cyan-500/40 shadow-[0_0_30px_rgba(0,240,255,0.15)] col-span-1 sm:col-span-2'
                  : 'border-slate-700/50 hover:border-slate-600/60'
              } ${status.glow}`}
              onClick={() => setExpandedCategory(isExpanded ? null : cat.category)}
            >
              {/* Card Header */}
              <div className={`p-4 bg-gradient-to-br ${meta.gradient} relative`}>
                {/* Top Row: Icon, Label, Count */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-3">
                    <div className={`p-2 rounded-lg ${status.bg} border`}>
                      {meta.icon}
                    </div>
                    <div>
                      <h3 className="font-display font-bold text-sm tracking-wider text-slate-100">
                        {meta.label.toUpperCase()}
                      </h3>
                      <span className={`text-[9px] font-mono uppercase tracking-widest ${
                        cat.total <= 1 ? 'text-red-400' : cat.total <= 5 ? 'text-amber-400' : 'text-emerald-400'
                      }`}>
                        {status.label}
                      </span>
                    </div>
                  </div>

                  {/* Radial Progress + Count */}
                  <div className="relative flex items-center justify-center">
                    <RadialProgress value={cat.total} max={maxCount} statusColor={status.ring} />
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                      <span className={`text-lg font-bold font-mono ${
                        cat.total <= 1 ? 'text-red-400' : cat.total <= 5 ? 'text-amber-400' : 'text-emerald-400'
                      }`}>
                        {cat.total}
                      </span>
                      <span className="text-[7px] text-slate-400 uppercase">playbooks</span>
                    </div>
                  </div>
                </div>

                {/* Source Badges */}
                {sourceBadges.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-3">
                    {sourceBadges.map(([src, cnt]) => (
                      <span
                        key={src}
                        className="px-2 py-0.5 rounded text-[8px] font-mono bg-slate-800/80 border border-slate-700/60 text-slate-300"
                      >
                        {src === 'generated' ? <Zap className="w-2.5 h-2.5 inline mr-0.5 text-violet-400" /> :
                         src === 'repo' ? <Database className="w-2.5 h-2.5 inline mr-0.5 text-cyan-400" /> :
                         <TrendingUp className="w-2.5 h-2.5 inline mr-0.5 text-emerald-400" />}
                        {src}: {cnt}
                      </span>
                    ))}
                  </div>
                )}

                {/* Confidence Tier Bar */}
                <ConfidenceBar tier={cat.by_confidence_tier} />

                {/* Expand Indicator */}
                <div className="absolute top-3 right-3">
                  {isExpanded
                    ? <ChevronUp className="w-4 h-4 text-cyan-400" />
                    : <ChevronDown className="w-4 h-4 text-slate-500" />
                  }
                </div>
              </div>

              {/* Expanded Tag Cloud */}
              {isExpanded && (
                <div className="p-4 border-t border-slate-700/40 bg-slate-900/40 animate-fadeIn">
                  <div className="flex items-center space-x-2 mb-3">
                    <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                    <h4 className="text-[10px] font-bold text-slate-300 uppercase tracking-widest font-mono">
                      TAG COVERAGE &mdash; {cat.distinct_tags.length} UNIQUE TAGS
                    </h4>
                  </div>

                  {cat.distinct_tags.length === 0 ? (
                    <div className="text-center py-4">
                      <AlertTriangle className="w-5 h-5 text-red-400 mx-auto mb-1" />
                      <p className="text-[10px] text-red-400 font-mono">NO TAGS &mdash; ZERO COVERAGE</p>
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                      {cat.distinct_tags.map((tagInfo) => {
                        const intensity = Math.min(tagInfo.count / (maxCount * 0.1), 1);
                        return (
                          <span
                            key={tagInfo.tag}
                            className="inline-flex items-center space-x-1 px-2 py-1 rounded-md text-[9px] font-mono border transition-all hover:scale-105"
                            style={{
                              backgroundColor: `rgba(0, 240, 255, ${0.03 + intensity * 0.12})`,
                              borderColor: `rgba(0, 240, 255, ${0.15 + intensity * 0.35})`,
                              color: `rgb(${200 - intensity * 100}, ${230 - intensity * 30}, ${240})`
                            }}
                          >
                            <span>{tagInfo.tag}</span>
                            <span className="text-[8px] px-1 py-0.5 rounded bg-slate-800/60 text-cyan-400 font-bold">{tagInfo.count}</span>
                          </span>
                        );
                      })}
                    </div>
                  )}

                  {/* Coverage Quality Indicators */}
                  <div className="mt-4 grid grid-cols-3 gap-2">
                    <div className="text-center p-2 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
                      <ShieldCheck className="w-4 h-4 text-emerald-400 mx-auto mb-1" />
                      <div className="text-xs font-bold text-emerald-400 font-mono">{cat.by_confidence_tier.trusted}</div>
                      <div className="text-[8px] text-slate-400 uppercase">Trusted</div>
                    </div>
                    <div className="text-center p-2 rounded-lg bg-amber-500/5 border border-amber-500/20">
                      <AlertTriangle className="w-4 h-4 text-amber-400 mx-auto mb-1" />
                      <div className="text-xs font-bold text-amber-400 font-mono">{cat.by_confidence_tier.low}</div>
                      <div className="text-[8px] text-slate-400 uppercase">Low Conf</div>
                    </div>
                    <div className="text-center p-2 rounded-lg bg-red-500/5 border border-red-500/20">
                      <Clock className="w-4 h-4 text-red-400 mx-auto mb-1" />
                      <div className="text-xs font-bold text-red-400 font-mono">{cat.by_confidence_tier.pending}</div>
                      <div className="text-[8px] text-slate-400 uppercase">Pending</div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* FOOTER STATUS */}
      <div className="glass-panel rounded-xl p-3 flex items-center justify-between">
        <div className="flex items-center space-x-2 text-[9px] text-slate-400 font-mono">
          <Database className="w-3.5 h-3.5 text-cyan-400" />
          <span>FORGE PLAYBOOK VAULT &bull; SQLite FTS5 INDEX &bull; {grandStats.total.toLocaleString()} PLAYBOOKS INDEXED</span>
        </div>
        <div className="flex items-center space-x-2 text-[9px] font-mono">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-emerald-400">WebSocket LIVE</span>
        </div>
      </div>
    </div>
  );
};
