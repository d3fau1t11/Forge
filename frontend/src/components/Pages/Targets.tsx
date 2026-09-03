import React, { useState } from 'react';
import { 
  Target as TargetIcon, 
  RefreshCw, 
  CheckCircle2, 
  Server, 
  ArrowDown, 
  Globe,
  Plus,
  Shield,
  Search,
  Activity
} from 'lucide-react';
import { Target } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

interface TargetsProps {
  targets: Target[];
  onRediscover: (id: string) => void;
  onVerify: (id: string) => void;
  onCreateTarget?: (targetData: {
    name: string;
    category: any;
    difficulty: any;
    target: string;
    description: string;
  }) => void;
  onNavigateTab?: (tab: any) => void;
}

export const Targets: React.FC<TargetsProps> = ({
  targets,
  onRediscover,
  onVerify,
  onCreateTarget,
  onNavigateTab
}) => {
  const [selectedTargetId, setSelectedTargetId] = useState<string>(targets[0]?.id || '');
  const [showModal, setShowModal] = useState(false);
  const [targetIp, setTargetIp] = useState('');
  const [targetName, setTargetName] = useState('');
  const [targetCategory, setTargetCategory] = useState<'WEB' | 'PWN' | 'REV' | 'CRYPTO' | 'FORENSICS' | 'RECON'>('WEB');
  const [targetDifficulty, setTargetDifficulty] = useState<'EASY' | 'MEDIUM' | 'HARD' | 'INSANE'>('MEDIUM');
  const [targetDesc, setTargetDesc] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  const activeTarget = targets.find((t) => t.id === selectedTargetId) || targets[0];

  const handleSelectTarget = (id: string) => {
    soundEngine.playClick();
    setSelectedTargetId(id);
  };

  const handleOpenModal = () => {
    soundEngine.playClick();
    setShowModal(true);
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetIp.trim() || !targetName.trim()) return;

    soundEngine.playFanfare();
    if (onCreateTarget) {
      onCreateTarget({
        name: targetName,
        category: targetCategory,
        difficulty: targetDifficulty,
        target: targetIp,
        description: targetDesc || `Target ${targetName} bound to IP ${targetIp}`
      });
    }

    setTargetIp('');
    setTargetName('');
    setTargetDesc('');
    setShowModal(false);
  };

  const filteredTargets = targets.filter(t => 
    !searchTerm || 
    t.currentIp.toLowerCase().includes(searchTerm.toLowerCase()) || 
    t.hostname.toLowerCase().includes(searchTerm.toLowerCase()) ||
    t.id.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="space-y-6 font-mono text-slate-100 pb-10">
      {/* Navigation Quick Bar */}
      <div className="glass-panel border border-slate-800 p-3.5 rounded-xl flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded-lg bg-obsidian-900 border border-cyber-cyan flex items-center justify-center text-cyber-cyan shadow-[0_0_15px_rgba(0,240,255,0.3)]">
            <TargetIcon className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-base font-display font-bold text-slate-100 uppercase tracking-wider neon-text-cyan">
              TARGET IDENTITY & INFRASTRUCTURE MATRIX
            </h1>
            <p className="text-[11px] text-slate-400">
              FORGE resolves target identities and monitors service port lineages across operations.
            </p>
          </div>
        </div>

        {/* Quick Navigation Buttons */}
        <div className="flex items-center space-x-2">
          {onNavigateTab && (
            <button
              onClick={() => { soundEngine.playTabSwitch(); onNavigateTab('command'); }}
              className="px-3 py-1.5 rounded-lg bg-obsidian-900 border border-slate-700 hover:border-cyber-cyan text-slate-300 hover:text-cyber-cyan text-xs font-bold flex items-center space-x-1.5 transition-colors"
            >
              <Activity className="w-3.5 h-3.5 text-cyber-cyan" />
              <span>COMMAND CENTER</span>
            </button>
          )}

          {onNavigateTab && (
            <button
              onClick={() => { soundEngine.playTabSwitch(); onNavigateTab('challenges'); }}
              className="px-3 py-1.5 rounded-lg bg-obsidian-900 border border-slate-700 hover:border-cyber-cyan text-slate-300 hover:text-cyber-cyan text-xs font-bold flex items-center space-x-1.5 transition-colors"
            >
              <Shield className="w-3.5 h-3.5 text-cyber-cyan" />
              <span>CHALLENGES</span>
            </button>
          )}

          <button
            onClick={handleOpenModal}
            className="px-4 py-1.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center space-x-1.5 shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all uppercase"
          >
            <Plus className="w-4 h-4" />
            <span>REGISTER TARGET</span>
          </button>
        </div>
      </div>

      {/* IF NO TARGETS REGISTERED YET */}
      {(!targets || targets.length === 0) ? (
        <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-10 flex flex-col items-center justify-center text-center space-y-5 shadow-[0_0_30px_rgba(0,240,255,0.15)] cyber-corner">
          <div className="w-16 h-16 rounded-2xl bg-obsidian-900 border border-cyber-cyan flex items-center justify-center text-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.4)] animate-pulse">
            <TargetIcon className="w-8 h-8" />
          </div>
          <div className="max-w-md space-y-2">
            <h2 className="text-xl font-display font-bold text-slate-100 uppercase neon-text-cyan">
              NO TARGET IDENTITIES REGISTERED
            </h2>
            <p className="text-xs text-slate-400 font-mono leading-relaxed">
              No target host IP or domain is active in the matrix. Register a new target identity or start a challenge to initiate target tracking.
            </p>
          </div>
          <div className="flex items-center space-x-3 pt-2">
            <button
              onClick={handleOpenModal}
              className="px-6 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center space-x-2 shadow-[0_0_20px_rgba(0,240,255,0.4)] hover:scale-105 transition-all uppercase tracking-wider"
            >
              <Plus className="w-4 h-4" />
              <span>REGISTER NEW TARGET</span>
            </button>

            {onNavigateTab && (
              <button
                onClick={() => { soundEngine.playTabSwitch(); onNavigateTab('challenges'); }}
                className="px-5 py-2.5 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-200 text-xs font-bold flex items-center space-x-2 transition-all uppercase"
              >
                <Shield className="w-4 h-4 text-cyber-cyan" />
                <span>GO TO CHALLENGES</span>
              </button>
            )}
          </div>
        </div>
      ) : (
        /* TARGET MATRIX & INSPECTOR */
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Targets Master List (1 col) */}
          <div className="glass-panel border border-slate-800 rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h2 className="text-xs font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
                KNOWN TARGET IDENTITIES ({filteredTargets.length})
              </h2>
              <span className="text-[10px] text-cyber-cyan font-bold px-2 py-0.5 rounded bg-obsidian-950 border border-cyber-cyan/40">
                {targets.length} TOTAL
              </span>
            </div>

            {/* Search Filter */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-2.5" />
              <input
                type="text"
                placeholder="Filter by IP / Host..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full bg-obsidian-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan transition-colors"
              />
            </div>

            <div className="space-y-2.5 max-h-[500px] overflow-y-auto pr-1">
              {filteredTargets.map((t) => (
                <div
                  key={t.id}
                  onClick={() => handleSelectTarget(t.id)}
                  className={`p-3.5 rounded-lg border text-xs cursor-pointer transition-all ${
                    t.id === (activeTarget?.id || selectedTargetId)
                      ? 'bg-obsidian-900/90 border-cyber-cyan text-slate-100 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                      : 'bg-obsidian-900/40 border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-bold text-cyber-cyan">{t.id}</span>
                    <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-950/80 border border-emerald-800 text-cyber-emerald font-bold">
                      ● {t.status}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-300 flex items-center justify-between">
                    <span>IP: <span className="font-bold text-slate-100">{t.currentIp}</span></span>
                    <span className="text-slate-500">({t.hostname})</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Target Details Inspector (2 cols) */}
          {activeTarget && (
            <div className="lg:col-span-2 glass-panel border border-slate-800 rounded-xl p-6 space-y-6">
              {/* Target Title & Controls Header */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
                <div>
                  <div className="flex items-center space-x-3 mb-1">
                    <span className="px-2.5 py-0.5 rounded bg-cyan-950/80 border border-cyber-cyan/50 text-cyber-cyan text-xs font-bold">
                      {activeTarget.id}
                    </span>
                    <h1 className="text-xl font-display font-bold text-slate-100 tracking-wider neon-text-cyan">{activeTarget.hostname}</h1>
                  </div>
                  <p className="text-xs text-slate-400">
                    Current Address: <span className="text-cyber-cyan font-bold">{activeTarget.currentIp}</span> • Discovery: <span className="text-slate-300">{activeTarget.discoveryMethod}</span>
                  </p>
                </div>

                {/* Actions */}
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => { soundEngine.playClick(); onVerify(activeTarget.id); }}
                    className="px-3.5 py-1.5 rounded-lg bg-emerald-950 hover:bg-emerald-900 border border-emerald-700 text-cyber-emerald font-bold text-xs flex items-center space-x-1.5 transition-colors"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>VERIFY</span>
                  </button>
                  <button
                    onClick={() => { soundEngine.playClick(); onRediscover(activeTarget.id); }}
                    className="px-3.5 py-1.5 rounded-lg bg-cyan-950 hover:bg-cyan-900 border border-cyber-cyan/60 text-cyber-cyan font-bold text-xs flex items-center space-x-1.5 transition-colors"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                    <span>REDISCOVER</span>
                  </button>
                </div>
              </div>

              {/* Lineage & Service Breakdown */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {/* IP Address Lineage History */}
                <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-4 space-y-3">
                  <h3 className="text-xs font-display font-bold text-slate-200 uppercase flex items-center space-x-2">
                    <Globe className="w-4 h-4 text-cyber-cyan" />
                    <span>ADDRESS HISTORY LINEAGE</span>
                  </h3>

                  <div className="p-3 bg-obsidian-900 border border-slate-800/80 rounded-lg space-y-2 font-mono text-xs">
                    {activeTarget.addressHistory.map((addr, idx) => (
                      <React.Fragment key={addr}>
                        <div className="flex items-center justify-between px-3 py-2 rounded bg-obsidian-950 border border-slate-800">
                          <span className="text-slate-200 font-bold">{addr}</span>
                          <span className="text-[10px] text-cyber-cyan font-bold">
                            {idx === activeTarget.addressHistory.length - 1 ? 'ACTIVE IP' : 'PREVIOUS'}
                          </span>
                        </div>
                        {idx < activeTarget.addressHistory.length - 1 && (
                          <div className="flex justify-center my-0.5 text-cyber-cyan">
                            <ArrowDown className="w-4 h-4 animate-bounce" />
                          </div>
                        )}
                      </React.Fragment>
                    ))}
                  </div>
                </div>

                {/* Verified Services List */}
                <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-4 space-y-3">
                  <h3 className="text-xs font-display font-bold text-slate-200 uppercase flex items-center space-x-2">
                    <Server className="w-4 h-4 text-cyber-cyan" />
                    <span>VERIFIED SERVICES & PORTS</span>
                  </h3>

                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {activeTarget.services.map((svc) => (
                      <div key={svc.port} className="p-3 rounded-lg bg-obsidian-900 border border-slate-800 text-xs flex items-center justify-between hover:border-cyber-cyan/30 transition-colors">
                        <div>
                          <div className="flex items-center space-x-2">
                            <span className="font-bold text-cyber-cyan">{svc.port}/{svc.proto}</span>
                            <span className="font-semibold text-slate-200">{svc.service}</span>
                          </div>
                          <span className="text-[10px] text-slate-400 block">{svc.version || 'Version unverified'}</span>
                        </div>
                        <span className="px-2 py-0.5 rounded bg-emerald-950/80 text-cyber-emerald border border-emerald-800 text-[10px] font-bold">
                          OPEN
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Technology Profile */}
              <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-4 space-y-2">
                <h3 className="text-xs font-display font-bold text-slate-200 uppercase">IDENTIFIED TECHNOLOGY STACK</h3>
                <div className="flex flex-wrap gap-2 pt-1">
                  {activeTarget.technologies.map((t) => (
                    <span key={t} className="px-3 py-1 rounded-lg bg-obsidian-900 border border-cyber-cyan/40 text-cyber-cyan text-xs font-semibold">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* TARGET INITIALIZATION MODAL */}
      {showModal && (
        <div className="fixed inset-0 bg-obsidian-950/80 backdrop-blur-md flex items-center justify-center p-4 z-50">
          <div className="glass-panel border-2 border-cyber-cyan/60 rounded-xl max-w-lg w-full p-6 space-y-5 shadow-[0_0_40px_rgba(0,240,255,0.25)] cyber-corner">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-2">
                <TargetIcon className="w-5 h-5 text-cyber-cyan" />
                <h2 className="text-sm font-display font-bold text-slate-100 uppercase neon-text-cyan">
                  REGISTER TARGET IDENTITY
                </h2>
              </div>
              <button 
                onClick={() => setShowModal(false)}
                className="text-slate-400 hover:text-slate-100 font-bold"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleFormSubmit} className="space-y-4 text-xs font-mono">
              <div>
                <label className="block text-slate-300 mb-1 font-bold">Target IP / Hostname *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 10.10.14.23 or target.ctf"
                  value={targetIp}
                  onChange={(e) => setTargetIp(e.target.value)}
                  className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan transition-colors"
                />
              </div>

              <div>
                <label className="block text-slate-300 mb-1 font-bold">Challenge / Target Name *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. VAULT_RECON"
                  value={targetName}
                  onChange={(e) => setTargetName(e.target.value)}
                  className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan transition-colors"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-slate-300 mb-1 font-bold">Category (Typeable)</label>
                  <input
                    type="text"
                    list="target-category-suggestions"
                    value={targetCategory}
                    onChange={(e) => setTargetCategory(e.target.value as any)}
                    placeholder="e.g. WEB, PWN, OSINT..."
                    className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan font-mono uppercase"
                  />
                  <datalist id="target-category-suggestions">
                    <option value="WEB" />
                    <option value="PWN" />
                    <option value="REV" />
                    <option value="CRYPTO" />
                    <option value="FORENSICS" />
                    <option value="RECON" />
                    <option value="OSINT" />
                    <option value="MISC" />
                    <option value="CLOUD" />
                  </datalist>
                </div>

                <div>
                  <label className="block text-slate-300 mb-1 font-bold">Difficulty</label>
                  <select
                    value={targetDifficulty}
                    onChange={(e) => setTargetDifficulty(e.target.value as any)}
                    className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan transition-colors"
                  >
                    <option value="EASY">EASY</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="HARD">HARD</option>
                    <option value="INSANE">INSANE</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-slate-300 mb-1 font-bold">Target Notes / Description</label>
                <textarea
                  rows={3}
                  placeholder="Target details, scope notes, or flag format (e.g. HTB{...})"
                  value={targetDesc}
                  onChange={(e) => setTargetDesc(e.target.value)}
                  className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan transition-colors resize-none"
                />
              </div>

              <div className="flex items-center justify-end space-x-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 rounded-lg bg-obsidian-900 border border-slate-700 text-slate-300 font-bold hover:bg-slate-800 transition-colors"
                >
                  CANCEL
                </button>
                <button
                  type="submit"
                  className="px-6 py-2 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all uppercase"
                >
                  INITIALIZE TARGET
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
