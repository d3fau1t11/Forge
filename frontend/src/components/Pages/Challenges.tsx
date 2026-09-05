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
  Trash2
} from 'lucide-react';
import { Challenge } from '../../types';
import { soundEngine } from '../../utils/soundEngine';
import { DirectoryBrowserModal } from './DirectoryBrowserModal';

interface ChallengesProps {
  challenges: Challenge[];
  onSelectChallenge: (challenge: Challenge) => void;
  onCreateChallenge: (newCh: { name: string; category: any; difficulty: any; target: string; description: string; workingDirectory?: string; platformName?: string }) => void;
  onToggleStatus: (id: string) => void;
  onDeleteChallenge?: (id: string) => void;
  onDeleteAllChallenges?: () => void;
}

export const Challenges: React.FC<ChallengesProps> = ({
  challenges,
  onSelectChallenge,
  onCreateChallenge,
  onToggleStatus,
  onDeleteChallenge,
  onDeleteAllChallenges
}) => {
  const [showModal, setShowModal] = useState(false);
  const [showDirBrowser, setShowDirBrowser] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('ALL');

  // Form states
  const [name, setName] = useState('');
  const [platformName, setPlatformName] = useState('PicoCTF');
  const [category, setCategory] = useState<string>('WEB');
  const [difficulty, setDifficulty] = useState<'EASY' | 'MEDIUM' | 'HARD' | 'INSANE'>('MEDIUM');
  const [description, setDescription] = useState('');
  const [customTargetOverride, setCustomTargetOverride] = useState('');
  const [showTargetOverride, setShowTargetOverride] = useState(false);
  const [workingDirectory, setWorkingDirectory] = useState('');

  const extractFolderName = (pathStr: string): string => {
    if (!pathStr) return '';
    const parts = pathStr.split(/[/\\]/).filter(Boolean);
    return parts.length > 0 ? parts[parts.length - 1] : pathStr;
  };

  const extractTargetFromText = (text: string): string => {
    if (!text) return '';
    // Match URL
    const urlMatch = text.match(/https?:\/\/[^\s]+/i);
    if (urlMatch) return urlMatch[0].replace(/[.,;)"'>]+$/, '');
    // Match nc <host> <port>
    const ncMatch = text.match(/nc\s+([a-zA-Z0-9.\-_]+)\s+(\d+)/i);
    if (ncMatch) return `${ncMatch[1]}:${ncMatch[2]}`;
    // Match IP:port or IP
    const ipMatch = text.match(/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b/);
    if (ipMatch) return ipMatch[0];
    // Match hostname:port
    const hostPortMatch = text.match(/\b([a-zA-Z0-9-]+\.[a-zA-Z0-9.\-]+:\d+)\b/);
    if (hostPortMatch) return hostPortMatch[0];
    // Match file path
    const fileMatch = text.match(/(?:[a-zA-Z]:[\\/]|(?:\/|~\/|\.\/))[^\s]+?\.(?:pcap|zip|bin|elf|tar|gz|py|c|exe|txt|raw)/i);
    if (fileMatch) return fileMatch[0];
    return '';
  };

  const detectedTarget = extractTargetFromText(description);
  const effectiveTarget = customTargetOverride.trim() || detectedTarget || (name.trim() ? `${name.trim().toLowerCase().replace(/\s+/g, '_')}.ctf` : '127.0.0.1');

  const handleWorkingDirectoryChange = (val: string) => {
    setWorkingDirectory(val);
    const derived = extractFolderName(val);
    if (derived) {
      setName(derived);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const folderName = extractFolderName(workingDirectory);
    const finalName = name.trim() || folderName || 'NEW_CHALLENGE';
    const targetToUse = effectiveTarget;
    soundEngine.playSuccess();
    onCreateChallenge({
      name: finalName,
      category,
      difficulty,
      target: targetToUse,
      description,
      workingDirectory,
      platformName: platformName.trim() || 'PicoCTF'
    });
    setName('');
    setPlatformName('PicoCTF');
    setDescription('');
    setCustomTargetOverride('');
    setShowTargetOverride(false);
    setWorkingDirectory('');
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

        <div className="flex items-center space-x-2 shrink-0">
          {challenges.length > 0 && onDeleteAllChallenges && (
            <button
              onClick={() => {
                if (window.confirm("Are you sure you want to clear ALL challenges from database?")) {
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
            onClick={() => { soundEngine.playClick(); setShowModal(true); }}
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
              <h3 className="text-sm font-display font-bold text-slate-200 uppercase tracking-wider">NO ACTIVE CTF CHALLENGES</h3>
              <p className="text-slate-400 max-w-md">No CTF challenges found in current database scope. Click below to initialize a real CTF target.</p>
            </div>
            <button
              onClick={() => { soundEngine.playClick(); setShowModal(true); }}
              className="px-5 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center space-x-2 shadow-[0_0_20px_rgba(0,240,255,0.4)] transition-all uppercase tracking-wider"
            >
              <Plus className="w-4 h-4" />
              <span>INITIALIZE NEW CHALLENGE</span>
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
                    <span className="font-bold text-slate-300 truncate max-w-[160px]" title={ch.workingDirectory || 'Default Workspace'}>
                      {ch.workingDirectory ? ch.workingDirectory.split(/[/\\]/).pop() || ch.workingDirectory : 'Auto-created'}
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
                    ></div>
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

      {/* New Challenge Modal Overlay */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-md p-4 animate-fadeIn">
          <div className="w-full max-w-xl max-h-[90vh] flex flex-col bg-obsidian-950 border-2 border-cyber-cyan/60 rounded-xl shadow-[0_0_60px_rgba(0,240,255,0.3)] cyber-corner overflow-hidden my-auto">
            {/* Modal Header */}
            <div className="p-5 border-b border-slate-800 flex items-center justify-between shrink-0 bg-obsidian-950">
              <h2 className="text-base font-display font-bold tracking-wider text-slate-100 flex items-center space-x-2 neon-text-cyan">
                <Shield className="w-5 h-5 text-cyber-cyan" />
                <span>INITIALIZE NEW CTF OPERATION</span>
              </h2>
            </div>

            {/* Scrollable Form Body */}
            <form onSubmit={handleSubmit} className="flex flex-col flex-1 min-h-0 overflow-hidden">
              <div className="p-5 space-y-4 text-xs overflow-y-auto flex-1 custom-scrollbar">
                {/* Mandatory System CTF Directory Path Preview */}
                <div className="p-3.5 rounded-lg bg-obsidian-900 border border-cyber-cyan/40 space-y-1.5">
                  <div className="flex items-center justify-between text-[11px] font-bold text-cyber-cyan uppercase">
                    <span className="flex items-center space-x-1.5">
                      <Folder className="w-3.5 h-3.5 text-cyber-cyan" />
                      <span>MANDATORY CTF WORKSPACE DIRECTORY</span>
                    </span>
                    <span className="text-[10px] text-emerald-400 bg-emerald-950/80 px-2 py-0.5 rounded border border-emerald-800 font-mono">
                      AUTO-ENFORCED
                    </span>
                  </div>
                  <div className="font-mono text-[11px] text-slate-200 bg-obsidian-950 p-2 rounded border border-slate-800 break-all select-all font-bold">
                    ~/Documents/CTF/
                    <span className="text-cyber-cyan">{platformName.trim() || 'PicoCTF'}</span>/
                    <span className="text-amber-400">{category.trim().toUpperCase() || 'WEB'}</span>/
                    <span className="text-rose-400">{difficulty}</span>/
                    <span className="text-emerald-400">{name.trim() || 'Challenge_Target'}</span>
                  </div>
                  <p className="text-[10px] text-slate-400">
                    System directory access locked. Workspace is automatically initialized under system Documents/CTF structured hierarchy.
                  </p>
                </div>

                <div>
                  <label className="block text-slate-400 uppercase mb-1 font-bold flex items-center justify-between">
                    <span>Challenge Name</span>
                    <span className="text-[10px] text-cyber-cyan font-normal">(e.g. Dolphin Cove, Web CTF 1)</span>
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="Enter Challenge Name..."
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono font-bold"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 uppercase mb-1 font-bold flex items-center justify-between">
                    <span>Platform / Competition Name</span>
                    <span className="text-[10px] text-cyber-cyan lowercase font-normal">(e.g. PicoCTF, HackTheBox)</span>
                  </label>
                  <input
                    type="text"
                    list="platform-suggestions"
                    placeholder="e.g. PicoCTF, HackTheBox, TryHackMe, DEF CON"
                    value={platformName}
                    onChange={(e) => setPlatformName(e.target.value)}
                    className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono"
                  />
                  <datalist id="platform-suggestions">
                    <option value="PicoCTF" />
                    <option value="HackTheBox" />
                    <option value="TryHackMe" />
                    <option value="DEF CON CTF" />
                    <option value="CyberSpace CTF" />
                  </datalist>
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
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-slate-400 uppercase font-bold flex items-center space-x-1.5">
                      <span>Challenge Brief, Targets & Files</span>
                      <span className="text-[10px] text-cyber-cyan font-normal">(URLs, IPs, netcat ports, files, hints)</span>
                    </label>
                    <button
                      type="button"
                      onClick={() => setShowTargetOverride(!showTargetOverride)}
                      className="text-[10px] text-cyber-cyan hover:underline"
                    >
                      {showTargetOverride ? 'Auto Target Mode' : 'Custom Target Override'}
                    </button>
                  </div>
                  <textarea
                    rows={4}
                    required
                    placeholder={`Paste the entire challenge brief or target details here:\n- Description / Story / Hints\n- Target URL (e.g. http://instance.picoctf.net:12345/ or nc host 1337)\n- Local or downloaded files (e.g. /home/user/downloads/chall.bin)`}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className="w-full bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono text-xs leading-relaxed"
                  ></textarea>

                  {/* Auto-detected target display badge */}
                  <div className="mt-2 flex items-center justify-between bg-obsidian-950 p-2.5 rounded border border-slate-800 text-[11px]">
                    <span className="text-slate-400 flex items-center space-x-1.5">
                      <TargetIcon className="w-3.5 h-3.5 text-cyber-cyan" />
                      <span>RESOLVED TARGET SCOPE:</span>
                    </span>
                    <span className="font-bold font-mono text-cyber-cyan truncate max-w-[280px]">
                      {effectiveTarget}
                    </span>
                  </div>

                  {/* Optional Custom Target Override */}
                  {showTargetOverride && (
                    <div className="mt-2 p-2.5 rounded bg-obsidian-900 border border-cyber-cyan/30 space-y-1">
                      <label className="block text-slate-400 text-[10px] uppercase font-bold">Manual Target Override (Optional)</label>
                      <input
                        type="text"
                        placeholder="e.g. http://10.10.14.23:8000"
                        value={customTargetOverride}
                        onChange={(e) => setCustomTargetOverride(e.target.value)}
                        className="w-full bg-obsidian-950 border border-slate-800 rounded px-2 py-1.5 text-slate-100 text-xs font-mono focus:outline-none focus:border-cyber-cyan"
                      />
                    </div>
                  )}
                </div>
              </div>

              {/* Sticky Action Footer */}
              <div className="p-4 bg-obsidian-950 border-t border-slate-800 flex items-center justify-end space-x-3 shrink-0">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 rounded-lg bg-obsidian-900 text-slate-400 hover:text-slate-200 text-xs font-bold transition-colors"
                >
                  CANCEL
                </button>
                <button
                  type="submit"
                  className="px-5 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs uppercase tracking-wider transition-all hover:scale-105 shadow-[0_0_15px_rgba(0,240,255,0.4)]"
                >
                  INITIALIZE CHALLENGE
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Directory Browser Modal */}
      {showDirBrowser && (
        <DirectoryBrowserModal
          initialPath={workingDirectory}
          onSelect={(selectedPath) => handleWorkingDirectoryChange(selectedPath)}
          onClose={() => setShowDirBrowser(false)}
        />
      )}
    </div>
  );
};

