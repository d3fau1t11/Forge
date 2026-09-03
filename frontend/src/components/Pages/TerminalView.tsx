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

interface TerminalViewProps {
  logs: TerminalLog[];
  onExecuteCommand?: (cmd: string) => void;
}

export const TerminalView: React.FC<TerminalViewProps> = ({
  logs,
  onExecuteCommand
}) => {
  const [activeTab, setActiveTab] = useState<'all' | 'forge_tools' | 'ai_cli'>('all');
  const [commandInput, setCommandInput] = useState('');
  const [terminalLogs, setTerminalLogs] = useState<TerminalLog[]>(logs);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  const handleCopy = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleClear = () => {
    setTerminalLogs([]);
  };

  const handleRun = (e: React.FormEvent) => {
    e.preventDefault();
    if (!commandInput.trim()) return;

    const newLog: TerminalLog = {
      id: `log-${Date.now()}`,
      timestamp: new Date().toISOString().substring(11, 19),
      type: commandInput.startsWith('claude') || commandInput.startsWith('codex') ? 'CLAUDE CODE' : 'FORGE TOOL EXECUTION',
      command: commandInput,
      output: `[+] Executing controlled command: ${commandInput}\n[+] Output generated cleanly.\n[+] Process finished with exit code 0.`,
      exitCode: 0,
      duration: '0.45s',
      privilege: 'SAFE',
      agent: 'OPERATOR'
    };

    setTerminalLogs((prev) => [...prev, newLog]);
    if (onExecuteCommand) onExecuteCommand(commandInput);
    setCommandInput('');
  };

  const filteredLogs = terminalLogs.filter((l) => {
    if (activeTab === 'forge_tools' && l.type !== 'FORGE TOOL EXECUTION') return false;
    if (activeTab === 'ai_cli' && l.type !== 'CLAUDE CODE' && l.type !== 'CODEX') return false;
    if (searchTerm && !l.command.toLowerCase().includes(searchTerm.toLowerCase()) && !l.output.toLowerCase().includes(searchTerm.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="space-y-4 font-mono text-slate-100 pb-8">
      {/* Distinction Header Banner */}
      <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded bg-cyan-950 border border-cyan-500 flex items-center justify-center text-cyan-400">
            <TerminalIcon className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-base font-bold text-slate-100 uppercase tracking-wider">INTEGRATED OPERATIONAL TERMINAL</h1>
            <div className="flex items-center space-x-3 text-[11px] text-slate-400 mt-0.5">
              <span>EXECUTION DISTINCTION:</span>
              <span className="text-cyan-300 font-bold px-1.5 py-0.2 rounded bg-cyan-950 border border-cyan-800">
                FORGE TOOL EXECUTION
              </span>
              <span>vs</span>
              <span className="text-emerald-300 font-bold px-1.5 py-0.2 rounded bg-emerald-950 border border-emerald-800">
                AI CLI PROVIDER (CLAUDE CODE / CODEX)
              </span>
            </div>
          </div>
        </div>

        {/* Search & Clear Controls */}
        <div className="flex items-center space-x-2">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2" />
            <input
              type="text"
              placeholder="Search terminal..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="bg-[#05080e] border border-slate-800 rounded pl-8 pr-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <button
            onClick={handleClear}
            className="px-2.5 py-1 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 text-xs font-bold flex items-center space-x-1"
          >
            <Trash2 className="w-3.5 h-3.5 text-red-400" />
            <span>CLEAR</span>
          </button>
        </div>
      </div>

      {/* Terminal Tabs */}
      <div className="flex items-center space-x-2 border-b border-slate-800 pb-1">
        <button
          onClick={() => setActiveTab('all')}
          className={`px-3 py-1.5 rounded text-xs font-bold transition-all uppercase ${
            activeTab === 'all'
              ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          [ ALL CONSOLES ]
        </button>

        <button
          onClick={() => setActiveTab('forge_tools')}
          className={`px-3 py-1.5 rounded text-xs font-bold transition-all uppercase ${
            activeTab === 'forge_tools'
              ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          [ FORGE TOOL EXECUTION ]
        </button>

        <button
          onClick={() => setActiveTab('ai_cli')}
          className={`px-3 py-1.5 rounded text-xs font-bold transition-all uppercase ${
            activeTab === 'ai_cli'
              ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/40'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          [ AI CLI PROVIDERS: CLAUDE CODE / CODEX ]
        </button>
      </div>

      {/* Terminal Output Window */}
      <div className="bg-[#05080e] border border-slate-800 rounded-lg p-4 font-mono text-xs space-y-4 max-h-[500px] overflow-y-auto">
        {filteredLogs.map((log) => (
          <div key={log.id} className="p-3 bg-[#080d17] border border-slate-900 rounded space-y-2">
            {/* Header Metadata Bar */}
            <div className="flex items-center justify-between border-b border-slate-800/80 pb-2 text-[11px]">
              <div className="flex items-center space-x-2">
                <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${
                  log.type === 'FORGE TOOL EXECUTION'
                    ? 'bg-cyan-950 text-cyan-400 border-cyan-800'
                    : log.type === 'SYSTEM'
                    ? 'bg-slate-900 text-slate-400 border-slate-800'
                    : 'bg-emerald-950 text-emerald-400 border-emerald-800'
                }`}>
                  {log.type}
                </span>
                {log.agent && (
                  <span className="text-slate-400">AGENT: <span className="text-slate-200 font-bold">{log.agent}</span></span>
                )}
                <span className="text-slate-500">{log.timestamp}</span>
              </div>

              <div className="flex items-center space-x-3 text-[10px]">
                <span>PRIVILEGE: <span className="text-emerald-400 font-bold">{log.privilege}</span></span>
                <span>DURATION: <span className="text-cyan-300 font-bold">{log.duration}</span></span>
                <span>EXIT: <span className={log.exitCode === 0 ? 'text-emerald-400 font-bold' : 'text-red-400 font-bold'}>{log.exitCode}</span></span>
                <button
                  onClick={() => handleCopy(log.id, `${log.command}\n${log.output}`)}
                  className="text-slate-400 hover:text-slate-100"
                >
                  {copiedId === log.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
            </div>

            {/* Command & Output */}
            <div>
              <div className="text-cyan-300 font-bold mb-1 flex items-center space-x-2">
                <span className="text-slate-500">forge@parrot:~$</span>
                <span>{log.command}</span>
              </div>
              <pre className="text-slate-300 text-[11px] leading-relaxed whitespace-pre-wrap font-mono pl-4 border-l-2 border-slate-800">
                {log.output}
              </pre>
            </div>
          </div>
        ))}
      </div>

      {/* Interactive Command Line Bar */}
      <form onSubmit={handleRun} className="flex items-center space-x-2 bg-[#0b1019] border border-cyan-500/40 p-2 rounded-lg">
        <span className="text-cyan-400 font-bold text-xs pl-2">forge@parrot:~$</span>
        <input
          type="text"
          placeholder="Execute controlled tool capability or CLI provider command (e.g. nmap -sV 10.10.14.23 or claude-code analyze)..."
          value={commandInput}
          onChange={(e) => setCommandInput(e.target.value)}
          className="flex-1 bg-transparent border-0 text-slate-100 text-xs font-mono focus:outline-none placeholder:text-slate-600"
        />
        <button
          type="submit"
          className="px-4 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs uppercase flex items-center space-x-1 shadow-[0_0_10px_rgba(6,182,212,0.3)]"
        >
          <Play className="w-3.5 h-3.5 fill-current" />
          <span>EXECUTE</span>
        </button>
      </form>
    </div>
  );
};
