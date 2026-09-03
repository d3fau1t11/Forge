import React from 'react';
import { 
  Play, 
  Target as TargetIcon, 
  Server, 
  Clock, 
  Cpu, 
  ChevronRight,
  Terminal as TerminalIcon,
  AlertOctagon
} from 'lucide-react';
import { Challenge, Target, AgentInfo, ProviderInfo } from '../../types';

interface CommandCenterProps {
  activeChallenge: Challenge;
  target: Target;
  agents: AgentInfo[];
  providers: ProviderInfo[];
  onOpenWorkspace: (challenge: Challenge) => void;
  onTriggerKillSwitch: () => void;
  killSwitchActive: boolean;
}

export const CommandCenter: React.FC<CommandCenterProps> = ({
  activeChallenge,
  target,
  agents,
  providers,
  onOpenWorkspace,
  onTriggerKillSwitch,
  killSwitchActive
}) => {
  const liveEvents = [
    { time: '18:42:01', source: 'ORCHESTRATOR', text: 'Target profile initialized (10.10.14.23)', color: 'text-cyan-400' },
    { time: '18:42:04', source: 'RECON', text: 'Capability requested: network_scan', color: 'text-emerald-400' },
    { time: '18:42:05', source: 'TOOL MANAGER', text: 'Selected nmap (-sV -sC -p 22,80,8080)', color: 'text-amber-400' },
    { time: '18:42:14', source: 'NMAP SCAN', text: 'Discovered: 22/tcp SSH, 80/tcp HTTP, 8080/tcp HTTP-Proxy', color: 'text-cyan-300' },
    { time: '18:42:17', source: 'WEB AGENT', text: 'New hypothesis: Investigate HTTP authentication endpoint /api/v1/auth', color: 'text-emerald-300' },
    { time: '18:42:20', source: 'WEB AGENT', text: 'CONFIRMED: SQL Injection on parameter "username"', color: 'text-red-400 font-bold' }
  ];

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* 1. Active Operation Card */}
      <div className="bg-[#0b1019] border border-cyan-500/30 rounded-lg p-5 shadow-[0_0_20px_rgba(6,182,212,0.1)] relative overflow-hidden">
        <div className="absolute top-0 right-0 w-64 h-64 bg-cyan-500/5 rounded-full blur-3xl pointer-events-none"></div>

        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center space-x-3 mb-1">
              <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-700 text-cyan-400 text-xs font-bold uppercase">
                {activeChallenge.category} CTF
              </span>
              <h1 className="text-xl font-bold tracking-wider text-slate-100">{activeChallenge.name}</h1>
              <span className="text-xs text-slate-400">• TARGET: <span className="text-cyan-300 font-bold">{target.currentIp}</span> ({target.hostname})</span>
            </div>
            <div className="flex items-center space-x-2 text-xs">
              <span className={`w-2.5 h-2.5 rounded-full ${killSwitchActive ? 'bg-red-500' : 'bg-emerald-400 animate-pulse'}`}></span>
              <span className={`font-bold ${killSwitchActive ? 'text-red-400' : 'text-emerald-400'}`}>
                {killSwitchActive ? '● OPERATIONS HALTED BY KILL SWITCH' : '● AUTONOMOUS OPERATION RUNNING'}
              </span>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={() => onOpenWorkspace(activeChallenge)}
              className="px-4 py-2 rounded bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs flex items-center space-x-2 shadow-[0_0_12px_rgba(6,182,212,0.3)] transition-all uppercase"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>OPEN CHALLENGE WORKSPACE</span>
            </button>
            <button
              onClick={onTriggerKillSwitch}
              className="px-4 py-2 rounded bg-red-900/60 hover:bg-red-800 border border-red-600 text-red-300 font-bold text-xs flex items-center space-x-1.5 transition-all uppercase"
            >
              <AlertOctagon className="w-3.5 h-3.5" />
              <span>STOP RUN</span>
            </button>
          </div>
        </div>

        {/* Operational Metrics Row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-4 text-xs">
          <div className="bg-[#070b12] border border-slate-800 rounded p-3">
            <span className="text-slate-400 text-[10px] uppercase block mb-1">Current Objective</span>
            <span className="text-cyan-300 font-semibold text-xs leading-snug block">Enumerating HTTP attack surface & SQL injection</span>
          </div>

          <div className="bg-[#070b12] border border-slate-800 rounded p-3">
            <span className="text-slate-400 text-[10px] uppercase block mb-1">Active AI Model</span>
            <span className="text-emerald-400 font-semibold text-xs block">Gemini 1.5 Pro + Cerebras</span>
          </div>

          <div className="bg-[#070b12] border border-slate-800 rounded p-3">
            <span className="text-slate-400 text-[10px] uppercase block mb-1">Next Action</span>
            <span className="text-amber-300 font-semibold text-xs block">Automated SQLi token extraction</span>
          </div>

          <div className="bg-[#070b12] border border-slate-800 rounded p-3">
            <span className="text-slate-400 text-[10px] uppercase block mb-1">Elapsed Time</span>
            <div className="flex items-center space-x-1 text-slate-100 font-bold text-sm">
              <Clock className="w-3.5 h-3.5 text-cyan-400" />
              <span>00:14:32</span>
            </div>
          </div>
        </div>
      </div>

      {/* Grid Layout: Agent Overview + Target Intelligence */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 2. Agent Overview Panel (2 cols) */}
        <div className="lg:col-span-2 bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center space-x-2">
              <Cpu className="w-4 h-4 text-cyan-400" />
              <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">AGENT OVERVIEW</h2>
            </div>
            <span className="text-[10px] text-slate-400">7 SPECIALIZED AGENTS REGISTERED</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {agents.map((ag) => (
              <div 
                key={ag.id} 
                className={`p-3 rounded border text-xs transition-all ${
                  ag.status === 'RUNNING' || ag.status === 'ANALYZING'
                    ? 'bg-cyan-950/20 border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.1)]'
                    : 'bg-[#070b12] border-slate-800/80 text-slate-400'
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center space-x-2">
                    <span className={`w-2 h-2 rounded-full ${
                      ag.status === 'RUNNING' ? 'bg-emerald-400 animate-pulse' :
                      ag.status === 'ANALYZING' ? 'bg-cyan-400 animate-pulse' :
                      'bg-slate-600'
                    }`}></span>
                    <span className="font-bold text-slate-200">{ag.name}</span>
                  </div>
                  <span className={`text-[10px] px-1.5 py-0.2 rounded font-bold uppercase ${
                    ag.status === 'RUNNING' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' :
                    ag.status === 'ANALYZING' ? 'bg-cyan-950 text-cyan-400 border border-cyan-800' :
                    'bg-slate-900 text-slate-500 border border-slate-800'
                  }`}>
                    {ag.status}
                  </span>
                </div>

                <div className="space-y-1 text-[11px]">
                  <p className="text-slate-300 line-clamp-1"><span className="text-slate-500">TASK:</span> {ag.currentObjective}</p>
                  <div className="flex items-center justify-between text-[10px] text-slate-400 pt-1 border-t border-slate-800/60">
                    <span>MODEL: <span className="text-cyan-300">{ag.selectedModel}</span></span>
                    <span>RUNTIME: {ag.runtime}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 4. Target Intelligence Summary Card (1 col) */}
        <div className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3 flex flex-col justify-between">
          <div>
            <div className="flex items-center space-x-2 border-b border-slate-800 pb-3 mb-3">
              <TargetIcon className="w-4 h-4 text-cyan-400" />
              <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">TARGET INTELLIGENCE</h2>
            </div>

            <div className="space-y-2.5 text-xs">
              <div className="flex justify-between border-b border-slate-800/50 pb-1.5">
                <span className="text-slate-400">Target IP:</span>
                <span className="font-bold text-cyan-300">{target.currentIp}</span>
              </div>
              <div className="flex justify-between border-b border-slate-800/50 pb-1.5">
                <span className="text-slate-400">Hostname:</span>
                <span className="text-slate-200">{target.hostname}</span>
              </div>
              <div className="flex justify-between border-b border-slate-800/50 pb-1.5">
                <span className="text-slate-400">Discovery Method:</span>
                <span className="text-slate-300 text-[11px]">{target.discoveryMethod}</span>
              </div>
              <div className="flex justify-between border-b border-slate-800/50 pb-1.5">
                <span className="text-slate-400">Last Verified:</span>
                <span className="text-emerald-400">{target.lastVerified}</span>
              </div>

              <div>
                <span className="text-slate-400 text-[11px] block mb-1">Open Services:</span>
                <div className="space-y-1">
                  {target.services.map((s) => (
                    <div key={s.port} className="flex items-center justify-between px-2 py-1 rounded bg-[#070b12] border border-slate-800 text-[11px]">
                      <span className="text-cyan-400 font-bold">{s.port}/{s.proto}</span>
                      <span className="text-slate-200 font-semibold">{s.service}</span>
                      <span className="text-slate-400 text-[10px]">{s.version}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <span className="text-slate-400 text-[11px] block mb-1">Technologies Stack:</span>
                <div className="flex flex-wrap gap-1">
                  {target.technologies.map((tech) => (
                    <span key={tech} className="px-1.5 py-0.5 rounded bg-slate-900 border border-slate-700 text-slate-300 text-[10px]">
                      {tech}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="pt-3 border-t border-slate-800">
            <button 
              onClick={() => onOpenWorkspace(activeChallenge)}
              className="w-full py-1.5 rounded bg-slate-900 hover:bg-slate-800 border border-cyan-500/40 text-cyan-300 text-xs font-semibold flex items-center justify-center space-x-1 transition-all"
            >
              <span>INSPECT TARGET PROFILE</span>
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Grid Layout: Live Operations Feed + Provider Status */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 3. Live Operations Feed (2 cols) */}
        <div className="lg:col-span-2 bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center space-x-2">
              <TerminalIcon className="w-4 h-4 text-cyan-400" />
              <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">LIVE OPERATIONS FEED</h2>
            </div>
            <div className="flex items-center space-x-2 text-[10px]">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
              <span className="text-slate-400 uppercase">STREAM ACTIVE</span>
            </div>
          </div>

          <div className="bg-[#05080e] border border-slate-900 rounded p-3 font-mono text-xs space-y-2 max-h-56 overflow-y-auto">
            {liveEvents.map((evt, idx) => (
              <div key={idx} className="flex items-start space-x-3 py-0.5 border-b border-slate-900/60 last:border-0">
                <span className="text-slate-500 text-[11px] shrink-0">{evt.time}</span>
                <span className="px-1.5 py-0.2 rounded bg-slate-900 text-slate-300 border border-slate-800 text-[10px] shrink-0 font-bold">
                  {evt.source}
                </span>
                <span className={`${evt.color} leading-tight`}>{evt.text}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 5. Provider Status Indicators (1 col) */}
        <div className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center space-x-2">
              <Server className="w-4 h-4 text-cyan-400" />
              <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">PROVIDER STATUS</h2>
            </div>
            <span className="text-[10px] text-emerald-400 font-bold">ALL HEALTHY</span>
          </div>

          <div className="space-y-2">
            {providers.slice(0, 7).map((p) => (
              <div key={p.name} className="flex items-center justify-between px-3 py-2 rounded bg-[#070b12] border border-slate-800/80 text-xs">
                <div className="flex items-center space-x-2">
                  <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                  <span className="font-semibold text-slate-200">{p.name}</span>
                </div>
                <div className="flex items-center space-x-3 text-[11px]">
                  <span className="text-slate-400">{p.latency}</span>
                  <span className="px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[9px] font-bold">
                    ONLINE
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
