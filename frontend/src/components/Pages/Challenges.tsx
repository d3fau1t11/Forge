import React, { useState } from 'react';
import { 
  Shield, 
  Plus, 
  Play, 
  Pause, 
  CheckCircle2, 
  Target as TargetIcon,
  Search,
  ChevronRight
} from 'lucide-react';
import { Challenge } from '../../types';

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
  const [category, setCategory] = useState<'WEB' | 'RECON' | 'CRYPTO' | 'FORENSICS' | 'PWN' | 'REV'>('WEB');
  const [difficulty, setDifficulty] = useState<'EASY' | 'MEDIUM' | 'HARD' | 'INSANE'>('MEDIUM');
  const [target, setTarget] = useState('');
  const [description, setDescription] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name || !target) return;
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

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Top Controls Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4 bg-[#0b1019] border border-slate-800 p-4 rounded-lg">
        <div className="flex items-center space-x-3 flex-1">
          <div className="relative flex-1 max-w-sm">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search challenges by name or IP..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-[#05080e] border border-slate-800 rounded pl-9 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="bg-[#05080e] border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-cyan-500"
          >
            <option value="ALL">ALL CATEGORIES</option>
            <option value="WEB">WEB</option>
            <option value="REV">REV</option>
            <option value="PWN">PWN</option>
            <option value="CRYPTO">CRYPTO</option>
            <option value="FORENSICS">FORENSICS</option>
            <option value="RECON">RECON</option>
          </select>
        </div>

        <button
          onClick={() => setShowModal(true)}
          className="px-4 py-2 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs flex items-center justify-center space-x-2 shadow-[0_0_12px_rgba(6,182,212,0.3)] transition-all uppercase"
        >
          <Plus className="w-4 h-4" />
          <span>[ NEW CHALLENGE ]</span>
        </button>
      </div>

      {/* Challenge Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredChallenges.map((ch) => (
          <div
            key={ch.id}
            className={`bg-[#0b1019] border rounded-lg p-4 flex flex-col justify-between space-y-4 transition-all hover:border-cyan-500/50 ${
              ch.status === 'RUNNING'
                ? 'border-cyan-500/40 shadow-[0_0_15px_rgba(6,182,212,0.1)]'
                : 'border-slate-800'
            }`}
          >
            {/* Header info */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 text-[10px] font-bold uppercase">
                  {ch.category}
                </span>
                <span className={`text-[10px] font-bold uppercase px-1.5 py-0.2 rounded border ${
                  ch.difficulty === 'HARD' || ch.difficulty === 'INSANE'
                    ? 'bg-red-950 text-red-400 border-red-800'
                    : ch.difficulty === 'MEDIUM'
                    ? 'bg-amber-950 text-amber-400 border-amber-800'
                    : 'bg-emerald-950 text-emerald-400 border-emerald-800'
                }`}>
                  {ch.difficulty}
                </span>
              </div>

              <h2 className="text-base font-bold text-slate-100 tracking-wider mb-1">{ch.name}</h2>
              <p className="text-xs text-slate-400 line-clamp-2">{ch.description || 'Target CTF challenge active in framework scope.'}</p>

              {/* Target & Flag status */}
              <div className="mt-3 space-y-1.5 text-xs">
                <div className="flex items-center justify-between text-slate-300">
                  <span className="text-slate-400 flex items-center space-x-1">
                    <TargetIcon className="w-3.5 h-3.5 text-cyan-400" />
                    <span>Target:</span>
                  </span>
                  <span className="font-bold text-cyan-300">{ch.target}</span>
                </div>

                <div className="flex items-center justify-between text-slate-300">
                  <span className="text-slate-400">Flag Status:</span>
                  {ch.flagStatus === 'CAPTURED' ? (
                    <span className="text-emerald-400 font-bold flex items-center space-x-1">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>CAPTURED</span>
                    </span>
                  ) : (
                    <span className="text-slate-400 font-semibold">UNFOUND</span>
                  )}
                </div>
              </div>

              {/* Progress bar */}
              <div className="mt-3 space-y-1">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-slate-400">Progress:</span>
                  <span className="text-cyan-400 font-bold">{ch.progress}%</span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-slate-900 overflow-hidden border border-slate-800">
                  <div
                    className="h-full bg-cyan-400 transition-all duration-300 shadow-[0_0_8px_rgba(6,182,212,0.5)]"
                    style={{ width: `${ch.progress}%` }}
                  ></div>
                </div>
              </div>
            </div>

            {/* Card Footer Controls */}
            <div className="pt-3 border-t border-slate-800/80 flex items-center justify-between">
              <button
                onClick={() => onToggleStatus(ch.id)}
                className={`px-2.5 py-1 rounded text-xs font-bold flex items-center space-x-1 border ${
                  ch.status === 'RUNNING'
                    ? 'bg-amber-950/60 border-amber-700 text-amber-300 hover:bg-amber-900'
                    : 'bg-emerald-950/60 border-emerald-700 text-emerald-300 hover:bg-emerald-900'
                }`}
              >
                {ch.status === 'RUNNING' ? (
                  <>
                    <Pause className="w-3 h-3" />
                    <span>PAUSE</span>
                  </>
                ) : (
                  <>
                    <Play className="w-3 h-3" />
                    <span>RESUME</span>
                  </>
                )}
              </button>

              <button
                onClick={() => onSelectChallenge(ch)}
                className="px-3 py-1 rounded bg-cyan-950 hover:bg-cyan-900 border border-cyan-500/50 text-cyan-300 text-xs font-bold flex items-center space-x-1 transition-all"
              >
                <span>OPEN WORKSPACE</span>
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* New Challenge Modal Overlay */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="w-full max-w-md bg-[#0d121c] border border-cyan-500/50 rounded-lg p-6 space-y-4 shadow-[0_0_40px_rgba(6,182,212,0.2)]">
            <h2 className="text-base font-bold tracking-wider text-slate-100 border-b border-slate-800 pb-3 flex items-center space-x-2">
              <Shield className="w-4 h-4 text-cyan-400" />
              <span>INITIALIZE NEW CTF CHALLENGE</span>
            </h2>

            <form onSubmit={handleSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block text-slate-400 uppercase mb-1">Challenge Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. VAULT_RECON"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full bg-[#05080e] border border-slate-800 rounded px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 uppercase mb-1">Category</label>
                  <select
                    value={category}
                    onChange={(e) => setCategory(e.target.value as any)}
                    className="w-full bg-[#05080e] border border-slate-800 rounded px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="WEB">WEB</option>
                    <option value="REV">REV</option>
                    <option value="PWN">PWN</option>
                    <option value="CRYPTO">CRYPTO</option>
                    <option value="FORENSICS">FORENSICS</option>
                    <option value="RECON">RECON</option>
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 uppercase mb-1">Difficulty</label>
                  <select
                    value={difficulty}
                    onChange={(e) => setDifficulty(e.target.value as any)}
                    className="w-full bg-[#05080e] border border-slate-800 rounded px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="EASY">EASY</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="HARD">HARD</option>
                    <option value="INSANE">INSANE</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-slate-400 uppercase mb-1">Target IP / Hostname</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 10.10.14.23 or target.ctf"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  className="w-full bg-[#05080e] border border-slate-800 rounded px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 uppercase mb-1">Description / Notes</label>
                <textarea
                  rows={3}
                  placeholder="Target details, CTF platform rules..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="w-full bg-[#05080e] border border-slate-800 rounded px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                ></textarea>
              </div>

              <div className="pt-3 border-t border-slate-800 flex items-center justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 rounded bg-slate-900 text-slate-400 hover:text-slate-200 text-xs font-bold"
                >
                  CANCEL
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs uppercase"
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
