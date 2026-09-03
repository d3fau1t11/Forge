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
  Filter
} from 'lucide-react';
import { Challenge } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

interface ChallengesProps {
  challenges: Challenge[];
  onSelectChallenge: (challenge: Challenge) => void;
  onCreateChallenge: (newCh: { name: string; category: any; difficulty: any; target: string; description: string }) => void;
  onToggleStatus: (id: string) => void;
}

export const Challenges: React.FC<ChallengesProps> = ({
  challenges,
  onSelectChallenge,
  onCreateChallenge,
  onToggleStatus
}) => {
  const [showModal, setShowModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('ALL');

  // Form states
  const [name, setName] = useState('');
  const [category, setCategory] = useState<string>('WEB');
  const [difficulty, setDifficulty] = useState<'EASY' | 'MEDIUM' | 'HARD' | 'INSANE'>('MEDIUM');
  const [target, setTarget] = useState('');
  const [description, setDescription] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name || !target) return;
    soundEngine.playSuccess();
    onCreateChallenge({ name, category, difficulty, target, description });
    setName('');
    setTarget('');
    setDescription('');
    setShowModal(false);
  };

  const filteredChallenges = challenges.filter((c) => {
    const matchesSearch = c.name.toLowerCase().includes(searchTerm.toLowerCase()) || c.target.includes(searchTerm);
    const matchesCat = categoryFilter === 'ALL' || c.category === categoryFilter;
    return matchesSearch && matchesCat;
  });

  const categories = ['ALL', 'WEB', 'PWN', 'REV', 'CRYPTO', 'FORENSICS', 'RECON'];

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

        <button
          onClick={() => { soundEngine.playClick(); setShowModal(true); }}
          className="px-5 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center justify-center space-x-2 shadow-[0_0_20px_rgba(0,240,255,0.4)] hover:scale-105 transition-all uppercase tracking-wider shrink-0"
        >
          <Plus className="w-4 h-4" />
          <span>[ NEW CHALLENGE ]</span>
        </button>
      </div>

      {/* Challenge Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {filteredChallenges.map((ch) => (
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
                <span className="px-2.5 py-1 rounded bg-cyan-950/80 border border-cyber-cyan/50 text-cyber-cyan text-[10px] font-bold uppercase tracking-wider">
                  {ch.category} CTF
                </span>
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
                  ></div>
                </div>
              </div>
            </div>

            {/* Card Footer Controls */}
            <div className="pt-4 border-t border-slate-800/80 flex items-center justify-between">
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

              <button
                onClick={() => { soundEngine.playClick(); onSelectChallenge(ch); }}
                className="px-4 py-1.5 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/30 border border-cyber-cyan/50 text-cyber-cyan text-xs font-bold flex items-center space-x-1.5 transition-all shadow-[0_0_10px_rgba(0,240,255,0.2)]"
              >
                <span>WORKSPACE</span>
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* New Challenge Modal Overlay */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-md p-4 animate-fadeIn">
          <div className="w-full max-w-lg bg-obsidian-950 border-2 border-cyber-cyan/60 rounded-xl p-6 space-y-5 shadow-[0_0_60px_rgba(0,240,255,0.3)] cyber-corner">
            <h2 className="text-lg font-display font-bold tracking-wider text-slate-100 border-b border-slate-800 pb-3 flex items-center space-x-2 neon-text-cyan">
              <Shield className="w-5 h-5 text-cyber-cyan" />
              <span>INITIALIZE NEW CTF OPERATION</span>
            </h2>

            <form onSubmit={handleSubmit} className="space-y-4 text-xs">
              <div>
                <label className="block text-slate-400 uppercase mb-1 font-bold">Challenge Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. HYDRA_AUTHENTICATION_BYPASS"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 uppercase mb-1 font-bold">Category (Typeable)</label>
                  <input
                    type="text"
                    list="category-suggestions"
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                    placeholder="e.g. WEB, PWN, OSINT, CLOUD..."
                    className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono uppercase"
                  />
                  <datalist id="category-suggestions">
                    <option value="WEB" />
                    <option value="PWN" />
                    <option value="REV" />
                    <option value="CRYPTO" />
                    <option value="FORENSICS" />
                    <option value="RECON" />
                    <option value="OSINT" />
                    <option value="MISC" />
                    <option value="CLOUD" />
                    <option value="BLOCKCHAIN" />
                  </datalist>
                </div>

                <div>
                  <label className="block text-slate-400 uppercase mb-1 font-bold">Difficulty</label>
                  <select
                    value={difficulty}
                    onChange={(e) => setDifficulty(e.target.value as any)}
                    className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono"
                  >
                    <option value="EASY">EASY</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="HARD">HARD</option>
                    <option value="INSANE">INSANE</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-slate-400 uppercase mb-1 font-bold">Target IP / Hostname</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 10.10.14.23 or target.ctf"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono"
                />
              </div>

              <div>
                <label className="block text-slate-400 uppercase mb-1 font-bold">Description / Rules</label>
                <textarea
                  rows={3}
                  placeholder="Target details, scope boundaries, platform rules..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono"
                ></textarea>
              </div>

              <div className="pt-4 border-t border-slate-800 flex items-center justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 rounded-lg bg-obsidian-900 text-slate-400 hover:text-slate-200 text-xs font-bold"
                >
                  CANCEL
                </button>
                <button
                  type="submit"
                  className="px-5 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs uppercase tracking-wider"
                >
                  INITIALIZE CHALLENGE
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

