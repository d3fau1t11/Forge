import React, { useState } from 'react';
import { 
  Terminal as TerminalIcon, 
  Search, 
  Trash2, 
  Copy, 
  Play, 
  Check 
} from 'lucide-react';
import { TerminalLog } from '../../types';
import { soundEngine } from '../../utils/soundEngine';
import { apiService } from '../../services/api';

interface TerminalViewProps {
  logs: TerminalLog[];
  activeChallengeId?: string;
  onExecuteCommand?: (cmd: string) => void;
}

export const TerminalView: React.FC<TerminalViewProps> = ({
  logs,
  activeChallengeId,
  onExecuteCommand
}) => {
  const [activeTab, setActiveTab] = useState<'all' | 'forge_tools' | 'ai_cli'>('all');
  const [commandInput, setCommandInput] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);

  const [userLogs, setUserLogs] = useState<TerminalLog[]>([]);

  const handleCopy = (id: string, text: string) => {
    soundEngine.playClick();
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleClear = () => {
    soundEngine.playClick();
    setUserLogs([]);
  };

  const handleRun = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!commandInput.trim() || isExecuting) return;

    soundEngine.playClick();
    const cmd = commandInput;
    setCommandInput('');
    setIsExecuting(true);

    try {
      await apiService.executeTerminalCommand(cmd, activeChallengeId);
    } catch (err) {
      console.warn('Fallback execution via API error:', err);
    } finally {
      setIsExecuting(false);
      if (onExecuteCommand) onExecuteCommand(cmd);
    }
  };

  const currentLogs = (logs && logs.length > 0 ? logs : userLogs) || [];

  const filteredLogs = currentLogs.filter((l) => {
    if (activeTab === 'forge_tools' && l.type !== 'FORGE TOOL EXECUTION' && l.type !== 'EXECUTION') return false;
    if (activeTab === 'ai_cli' && l.type !== 'CLAUDE CODE' && l.type !== 'CODEX') return false;
    if (searchTerm && !l.command.toLowerCase().includes(searchTerm.toLowerCase()) && !l.output.toLowerCase().includes(searchTerm.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-10">
      {/* Distinction Header Banner */}
      <div className="glass-panel border border-slate-800 p-5 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-lg bg-obsidian-900 border border-cyber-cyan flex items-center justify-center text-cyber-cyan shadow-[0_0_15px_rgba(0,240,255,0.3)]">
            <TerminalIcon className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-display font-bold text-slate-100 uppercase tracking-wider neon-text-cyan">
              INTEGRATED OPERATIONAL TERMINAL
            </h1>
            <div className="flex items-center space-x-3 text-[11px] text-slate-400 mt-1">
              <span>EXECUTION DISTINCTION:</span>
              <span className="text-cyber-cyan font-bold px-2 py-0.5 rounded bg-cyan-950/80 border border-cyber-cyan/50">
                FORGE TOOL EXECUTION
              </span>
              <span>vs</span>
              <span className="text-cyber-emerald font-bold px-2 py-0.5 rounded bg-emerald-950/80 border border-emerald-800">
                AI CLI PROVIDER (CLAUDE CODE / CODEX)
              </span>
            </div>
          </div>
        </div>

        {/* Search & Clear Controls */}
        <div className="flex items-center space-x-3">
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search stream..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="bg-obsidian-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan transition-colors"
            />
          </div>

          <button
            onClick={handleClear}
            className="px-3 py-1.5 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-300 text-xs font-bold flex items-center space-x-1.5 transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5 text-cyber-rose" />
            <span>CLEAR</span>
          </button>
        </div>
      </div>

      {/* Terminal Tabs */}
      <div className="flex items-center space-x-2 border-b border-slate-800 pb-2">
        <button
          onClick={() => { soundEngine.playClick(); setActiveTab('all'); }}
          className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase ${
            activeTab === 'all'
              ? 'bg-cyber-cyan text-obsidian-950 font-display shadow-[0_0_15px_rgba(0,240,255,0.4)]'
              : 'text-slate-400 hover:text-slate-200 bg-obsidian-900/60 border border-slate-800'
          }`}
        >
          [ ALL CONSOLES ]
        </button>

        <button
          onClick={() => { soundEngine.playClick(); setActiveTab('forge_tools'); }}
          className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase ${
            activeTab === 'forge_tools'
              ? 'bg-cyber-cyan text-obsidian-950 font-display shadow-[0_0_15px_rgba(0,240,255,0.4)]'
              : 'text-slate-400 hover:text-slate-200 bg-obsidian-900/60 border border-slate-800'
          }`}
        >
          [ FORGE TOOL EXECUTION ]
        </button>

        <button
          onClick={() => { soundEngine.playClick(); setActiveTab('ai_cli'); }}
          className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase ${
            activeTab === 'ai_cli'
              ? 'bg-cyber-emerald text-obsidian-950 font-display shadow-[0_0_15px_rgba(16,185,129,0.4)]'
              : 'text-slate-400 hover:text-slate-200 bg-obsidian-900/60 border border-slate-800'
          }`}
        >
          [ AI CLI PROVIDERS: CLAUDE CODE / CODEX ]
        </button>
      </div>

      {/* Terminal Output Window */}
      <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-5 font-mono text-xs space-y-4 max-h-[520px] overflow-y-auto shadow-inner">
        {filteredLogs.map((log) => (
          <div key={log.id} className="p-4 bg-obsidian-900 border border-slate-800/90 rounded-lg space-y-2.5 hover:border-cyber-cyan/30 transition-colors">
            {/* Header Metadata Bar */}
            <div className="flex items-center justify-between border-b border-slate-800 pb-2 text-[11px]">
              <div className="flex items-center space-x-2">
                <span className={`px-2.5 py-0.5 rounded text-[10px] font-bold uppercase border ${
                  log.type === 'FORGE TOOL EXECUTION' || log.type === 'EXECUTION'
                    ? 'bg-cyan-950 text-cyber-cyan border-cyber-cyan/50'
                    : log.type === 'SYSTEM'
                    ? 'bg-slate-900 text-slate-400 border-slate-800'
                    : 'bg-emerald-950 text-cyber-emerald border-emerald-800'
                }`}>
                  {log.type}
                </span>
                {log.agent && (
                  <span className="text-slate-400 font-bold">AGENT: <span className="text-slate-200">{log.agent}</span></span>
                )}
                <span className="text-slate-500">{log.timestamp}</span>
              </div>

              <div className="flex items-center space-x-3 text-[10px]">
                <span>PRIVILEGE: <span className="text-cyber-emerald font-bold">{log.privilege || 'SAFE'}</span></span>
                <span>DURATION: <span className="text-cyber-cyan font-bold">{log.duration || '0.5s'}</span></span>
                <span>EXIT: <span className={log.exitCode === 0 ? 'text-cyber-emerald font-bold' : 'text-cyber-rose font-bold'}>{log.exitCode}</span></span>
                <button
                  onClick={() => handleCopy(log.id, `${log.command}\n${log.output}`)}
                  className="text-slate-400 hover:text-slate-100 transition-colors"
                >
                  {copiedId === log.id ? <Check className="w-4 h-4 text-cyber-emerald" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {/* Command & Output */}
            <div>
              <div className="text-cyber-cyan font-bold mb-1.5 flex items-center space-x-2">
                <span className="text-slate-500 font-bold">forge@parrot:~$</span>
                <span>{log.command}</span>
              </div>
              <pre className="text-slate-300 text-[11px] leading-relaxed whitespace-pre-wrap font-mono pl-4 border-l-2 border-cyber-cyan/40 bg-obsidian-950 p-3 rounded border border-slate-900">
                {log.output}
              </pre>
            </div>
          </div>
        ))}
      </div>

      {/* Interactive Command Line Bar */}
      <form onSubmit={handleRun} className="flex items-center space-x-3 glass-panel border-2 border-cyber-cyan/40 p-2.5 rounded-xl shadow-[0_0_20px_rgba(0,240,255,0.15)] cyber-corner">
        <span className="text-cyber-cyan font-bold text-xs pl-2 font-mono">forge@parrot:~$</span>
        <input
          type="text"
          placeholder="Execute controlled tool capability or CLI provider command (e.g. nmap -sV 10.10.14.23 or python3 script.py)..."
          value={commandInput}
          disabled={isExecuting}
          onChange={(e) => setCommandInput(e.target.value)}
          className="flex-1 bg-transparent border-0 text-slate-100 text-xs font-mono focus:outline-none placeholder:text-slate-600 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={isExecuting}
          className="px-5 py-2 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs uppercase flex items-center space-x-2 shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all disabled:opacity-50"
        >
          <Play className="w-3.5 h-3.5 fill-current" />
          <span>{isExecuting ? 'EXECUTING...' : 'EXECUTE'}</span>
        </button>
      </form>
    </div>
  );
};


