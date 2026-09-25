import React, { useState } from 'react';
import {
  Shield,
  Plus,
  Play,
  Pause,
  CheckCircle2,
  Target as TargetIcon,
  Search,
  ChevronRight,
  Filter,
  Folder,
  Trash2,
  X,
} from 'lucide-react';
import { Challenge } from '../../types';
import { soundEngine } from '../../utils/soundEngine';
import { NewChallengeChat } from './NewChallengeChat';

interface ChallengesProps {
  challenges: Challenge[];
  onSelectChallenge: (challenge: Challenge) => void;
  onToggleStatus: (id: string) => void;
  onDeleteChallenge?: (id: string) => void;
  onDeleteAllChallenges?: () => void;
  onRefreshBackendData?: () => Promise<void> | void;
}

export const Challenges: React.FC<ChallengesProps> = ({
  challenges,
  onSelectChallenge,
  onToggleStatus,
  onDeleteChallenge,
  onDeleteAllChallenges,
  onRefreshBackendData,
}) => {
  const [showChatModal, setShowChatModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('ALL');

  const filteredChallenges = challenges.filter((c) => {
    const matchesSearch =
      c.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      c.target.includes(searchTerm);
    const matchesCat = categoryFilter === 'ALL' || c.category === categoryFilter;
    return matchesSearch && matchesCat;
  });

  const categories = ['ALL', 'WEB', 'PWN', 'REV', 'CRYPTO', 'FORENSICS', 'RECON'];

  const handleChatCreated = (challenge: Challenge) => {
    setShowChatModal(false);
    onSelectChallenge(challenge);
  };

  const handleOpenModal = () => {
    soundEngine.playClick();
    setShowChatModal(true);
  };

  const handleCloseModal = () => {
    soundEngine.playClick();
    setShowChatModal(false);
  };

  return (
    <div className="space-y-6 font-mono text-slate-100 pb-10">
      {/* Top Controls Bar */}
      <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4 glass-panel p-4 rounded-xl border border-slate-800">
        <div className="flex items-center space-x-3 flex-1 flex-wrap gap-y-2">
          {/* Search Field */}
          <div className="relative flex-1 min-w-[240px]">
            <Search className="w-4 h-4 text-cyber-cyan absolute left-3 top-3" />
            <input
              type="text"
              placeholder="Filter challenges by name or IP..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-obsidian-950 border border-slate-800 rounded-lg pl-9 pr-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan transition-colors"
            />
          </div>

          {/* Category Quick Filter Pills */}
          <div className="flex items-center space-x-1.5 overflow-x-auto py-1">
            <Filter className="w-3.5 h-3.5 text-slate-400 mr-1 hidden lg:block" />
            {categories.map((cat) => (
              <button
                key={cat}
                onClick={() => { soundEngine.playClick(); setCategoryFilter(cat); }}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                  categoryFilter === cat
                    ? 'bg-cyber-cyan text-obsidian-950 shadow-[0_0_12px_rgba(0,240,255,0.4)]'
                    : 'bg-obsidian-900 text-slate-400 hover:text-slate-200 border border-slate-800'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center space-x-2 shrink-0">
          {challenges.length > 0 && onDeleteAllChallenges && (
            <button
              onClick={() => {
                if (window.confirm('Are you sure you want to clear ALL challenges from database?')) {
                  soundEngine.playAlarm();
                  onDeleteAllChallenges();
                }
              }}
              className="px-3.5 py-2.5 rounded-lg bg-rose-950/70 hover:bg-rose-900 border border-rose-800 text-rose-300 font-display font-bold text-xs flex items-center justify-center space-x-1.5 transition-all uppercase shrink-0"
            >
              <Trash2 className="w-4 h-4 text-cyber-rose" />
              <span>[ CLEAR ALL ]</span>
            </button>
          )}

          <button
            id="new-challenge-btn"
            onClick={handleOpenModal}
            className="px-5 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center justify-center space-x-2 shadow-[0_0_20px_rgba(0,240,255,0.4)] hover:scale-105 transition-all uppercase tracking-wider shrink-0"
          >
            <Plus className="w-4 h-4" />
            <span>[ NEW CHALLENGE ]</span>
          </button>
        </div>
      </div>

      {/* Challenge Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {filteredChallenges.length === 0 ? (
          <div className="col-span-full glass-panel border border-slate-800 rounded-xl p-10 flex flex-col items-center justify-center text-center space-y-4 text-xs font-mono">
            <Shield className="w-12 h-12 text-cyber-cyan/40 animate-pulse" />
            <div className="space-y-1">
              <h3 className="text-sm font-display font-bold text-slate-200 uppercase tracking-wider">
                NO ACTIVE CTF CHALLENGES
              </h3>
              <p className="text-slate-400 max-w-md">
                No CTF challenges found in current database scope. Click below to initialize a real CTF target.
              </p>
            </div>
            <button
              onClick={handleOpenModal}
              className="mt-2 px-5 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center space-x-2 shadow-[0_0_20px_rgba(0,240,255,0.4)] transition-all uppercase tracking-wider hover:scale-105"
            >
              <Plus className="w-4 h-4" />
              <span>NEW CHALLENGE</span>
            </button>
          </div>
        ) : (
          filteredChallenges.map((ch) => (
            <div
              key={ch.id}
              className={`glass-panel rounded-xl p-5 flex flex-col justify-between space-y-4 transition-all hover:scale-[1.02] cyber-corner ${
                ch.status === 'RUNNING'
                  ? 'border-2 border-cyber-cyan/50 shadow-[0_0_20px_rgba(0,240,255,0.15)]'
                  : 'border border-slate-800'
              }`}
            >
              {/* Header info */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center space-x-1.5 flex-wrap gap-y-1">
                    <span className="px-2.5 py-1 rounded bg-cyan-950/80 border border-cyber-cyan/50 text-cyber-cyan text-[10px] font-bold uppercase tracking-wider">
                      {ch.category} CTF
                    </span>
                    {ch.platformName && (
                      <span className="px-2 py-1 rounded bg-purple-950/80 border border-purple-700/60 text-purple-300 text-[10px] font-bold tracking-wider">
                        {ch.platformName}
                      </span>
                    )}
                  </div>
                  <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded border ${
                    ch.difficulty === 'HARD' || ch.difficulty === 'INSANE'
                      ? 'bg-rose-950 text-cyber-rose border-rose-800'
                      : ch.difficulty === 'MEDIUM'
                      ? 'bg-amber-950 text-cyber-amber border-amber-800'
                      : 'bg-emerald-950 text-cyber-emerald border-emerald-800'
                  }`}>
                    {ch.difficulty}
                  </span>
                </div>

                <h2 className="text-lg font-display font-bold text-slate-100 tracking-wider mb-1.5 neon-text-cyan">
                  {ch.name}
                </h2>
                <p className="text-xs text-slate-400 line-clamp-2 leading-relaxed">
                  {ch.description || 'Target CTF challenge active in framework scope.'}
                </p>

                {/* Target & Flag status */}
                <div className="mt-4 space-y-2 text-xs bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <div className="flex items-center justify-between text-slate-300">
                    <span className="text-slate-400 flex items-center space-x-1.5">
                      <TargetIcon className="w-3.5 h-3.5 text-cyber-cyan" />
                      <span>Target IP:</span>
                    </span>
                    <span className="font-bold text-cyber-cyan">{ch.target}</span>
                  </div>

                  <div className="flex items-center justify-between text-slate-300">
                    <span className="text-slate-400 flex items-center space-x-1.5">
                      <Folder className="w-3.5 h-3.5 text-cyber-cyan" />
                      <span>Workspace Dir:</span>
                    </span>
                    <span
                      className="font-bold text-slate-300 truncate max-w-[160px]"
                      title={ch.workingDirectory || 'Default Workspace'}
                    >
                      {ch.workingDirectory
                        ? ch.workingDirectory.split(/[/\\]/).pop() || ch.workingDirectory
                        : 'Auto-created'}
                    </span>
                  </div>

                  <div className="flex items-center justify-between text-slate-300">
                    <span className="text-slate-400">Flag Capture:</span>
                    {ch.flagStatus === 'CAPTURED' ? (
                      <span className="text-cyber-emerald font-bold flex items-center space-x-1">
                        <CheckCircle2 className="w-4 h-4" />
                        <span>CAPTURED</span>
                      </span>
                    ) : (
                      <span className="text-slate-400 font-semibold">UNFOUND</span>
                    )}
                  </div>
                </div>

                {/* Progress bar */}
                <div className="mt-4 space-y-1.5">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-400 font-bold">REASONING PROGRESS:</span>
                    <span className="text-cyber-cyan font-bold">{ch.progress}%</span>
                  </div>
                  <div className="w-full h-2 rounded-full bg-obsidian-950 overflow-hidden border border-slate-800">
                    <div
                      className="h-full bg-cyber-cyan transition-all duration-500 shadow-[0_0_10px_#00f0ff]"
                      style={{ width: `${ch.progress}%` }}
                    />
                  </div>
                </div>
              </div>

              {/* Card Footer Controls */}
              <div className="pt-4 border-t border-slate-800/80 flex items-center justify-between gap-2">
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => { soundEngine.playClick(); onToggleStatus(ch.id); }}
                    className={`px-3 py-1.5 rounded-lg text-xs font-bold flex items-center space-x-1.5 border transition-all ${
                      ch.status === 'RUNNING'
                        ? 'bg-amber-950/60 border-amber-700 text-cyber-amber hover:bg-amber-900'
                        : 'bg-emerald-950/60 border-emerald-700 text-cyber-emerald hover:bg-emerald-900'
                    }`}
                  >
                    {ch.status === 'RUNNING' ? (
                      <>
                        <Pause className="w-3.5 h-3.5" />
                        <span>PAUSE</span>
                      </>
                    ) : (
                      <>
                        <Play className="w-3.5 h-3.5" />
                        <span>RESUME</span>
                      </>
                    )}
                  </button>

                  {onDeleteChallenge && (
                    <button
                      onClick={() => {
                        if (window.confirm(`Delete challenge "${ch.name}"?`)) {
                          soundEngine.playClick();
                          onDeleteChallenge(ch.id);
                        }
                      }}
                      className="p-1.5 rounded-lg bg-rose-950/40 hover:bg-rose-900/60 border border-rose-800/60 text-cyber-rose hover:text-white transition-all"
                      title="Delete Challenge"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>

                <button
                  onClick={() => { soundEngine.playClick(); onSelectChallenge(ch); }}
                  className="px-4 py-1.5 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/30 border border-cyber-cyan/50 text-cyber-cyan text-xs font-bold flex items-center space-x-1.5 transition-all shadow-[0_0_10px_rgba(0,240,255,0.2)]"
                >
                  <span>WORKSPACE</span>
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* â”€â”€ New Challenge Chat Modal Overlay â”€â”€ */}
      {showChatModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-md p-4 animate-fadeIn">
          <div className="w-full max-w-5xl max-h-[92vh] flex flex-col bg-obsidian-950 border-2 border-cyber-cyan/40 rounded-2xl shadow-[0_0_80px_rgba(0,240,255,0.2)] overflow-hidden">
            {/* Thin close-button strip â€” chat content owns the real header */}
            <div className="flex items-center justify-end px-4 pt-3 pb-1 shrink-0">
              <button
                onClick={handleCloseModal}
                className="p-2 rounded-lg border border-slate-800 hover:border-cyber-rose/60 bg-obsidian-900 text-slate-400 hover:text-cyber-rose transition-colors"
                title="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Chat component fills the remaining height */}
            <div className="flex-1 overflow-hidden px-4 pb-4">
              <NewChallengeChat
                onOpenWorkspace={handleChatCreated}
                onRefreshBackendData={onRefreshBackendData}
                onClose={handleCloseModal}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

