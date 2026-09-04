import React, { useState, useEffect } from 'react';
import { 
  Play, 
  Target as TargetIcon, 
  Server, 
  Clock, 
  Cpu, 
  ChevronRight,
  Terminal as TerminalIcon,
  AlertOctagon,
  Radar,
  Zap,
  Radio,
  Sparkles
} from 'lucide-react';
import { Challenge, Target, AgentInfo, ProviderInfo } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

interface CommandCenterProps {
  activeChallenge?: Challenge | null;
  target?: Target | null;
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
  const [quickNotice, setQuickNotice] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);

  useEffect(() => {
    let interval: any = null;
    if (activeChallenge && activeChallenge.status === 'RUNNING' && !killSwitchActive) {
      interval = setInterval(() => {
        setElapsedSeconds((prev) => prev + 1);
      }, 1000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [activeChallenge?.status, killSwitchActive]);

  const formatDuration = (totalSeconds: number): string => {
    const hrs = Math.floor(totalSeconds / 3600);
    const mins = Math.floor((totalSeconds % 3600) / 60);
    const secs = totalSeconds % 60;
    return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const liveEvents = activeChallenge ? [
    { time: '00:00:01', source: 'ORCHESTRATOR', text: `Target profile initialized (${target?.currentIp || '127.0.0.1'})`, color: 'text-cyber-cyan' },
    { time: '00:00:03', source: 'RECON', text: 'Capability requested: network_scan', color: 'text-cyber-emerald' },
    { time: '00:00:05', source: 'TOOL MANAGER', text: 'Standby for tool invocation', color: 'text-cyber-amber' },
  ] : [
    { time: '00:00:00', source: 'SYSTEM', text: 'FORGE Engine online. Standing by for new challenge registration...', color: 'text-cyber-cyan font-bold' }
  ];

  const handleQuickAction = (actionName: string) => {
    soundEngine.playClick();
    setQuickNotice(`[COMMAND SENT] ${actionName} triggered on target ${target?.currentIp || 'SYSTEM'}`);
    setTimeout(() => setQuickNotice(null), 3500);
  };

  return (
    <div className="space-y-6 font-mono text-slate-100 pb-10">
      {/* Quick Action Toast Notice */}
      {quickNotice && (
        <div className="p-3 bg-obsidian-900 border border-cyber-cyan text-cyber-cyan rounded-lg text-xs flex items-center space-x-2 shadow-[0_0_20px_rgba(0,240,255,0.4)] animate-bounce">
          <Sparkles className="w-4 h-4 text-cyber-cyan animate-spin" />
          <span className="font-bold">{quickNotice}</span>
        </div>
      )}

      {/* 1. Active Operation Banner Card / Empty State */}
      {activeChallenge ? (
        <div className="glass-panel border-2 border-cyber-cyan/30 rounded-xl p-6 shadow-[0_0_30px_rgba(0,240,255,0.15)] relative overflow-hidden cyber-corner">
          <div className="absolute -top-10 -right-10 w-72 h-72 bg-cyber-cyan/10 rounded-full blur-3xl pointer-events-none"></div>

          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-5 pb-5 border-b border-slate-800/80">
            <div>
              <div className="flex items-center space-x-3 mb-2 flex-wrap gap-y-1">
                <span className="px-2.5 py-1 rounded bg-cyan-950/80 border border-cyber-cyan/60 text-cyber-cyan text-xs font-display font-bold tracking-widest uppercase">
                  {activeChallenge.category} CTF TARGET
                </span>
                <h1 className="text-2xl font-display font-bold tracking-wider text-slate-100 neon-text-cyan">
                  {activeChallenge.name}
                </h1>
                {target && (
                  <span className="text-xs text-slate-400 font-mono">
                    • TARGET: <span className="text-cyber-cyan font-bold">{target.currentIp}</span> ({target.hostname})
                  </span>
                )}
              </div>

              <div className="flex items-center space-x-3 text-xs">
                <span className={`w-3 h-3 rounded-full ${killSwitchActive ? 'bg-cyber-rose' : 'bg-cyber-emerald animate-ping'}`}></span>
                <span className={`font-bold tracking-wider ${killSwitchActive ? 'text-cyber-rose' : 'text-cyber-emerald'}`}>
                  {killSwitchActive ? '● ENGINE LOCKDOWN (HALTED BY OPERATOR)' : '● AUTONOMOUS REASONING LOOP RUNNING'}
                </span>
              </div>
            </div>

            <div className="flex items-center space-x-3">
              <button
                onClick={() => { soundEngine.playClick(); onOpenWorkspace(activeChallenge); }}
                className={`px-5 py-2.5 rounded-lg font-display font-bold text-xs flex items-center space-x-2 transition-all uppercase tracking-wider ${
                  activeChallenge.status === 'RUNNING'
                    ? 'bg-cyber-emerald hover:bg-emerald-400 text-obsidian-950 shadow-[0_0_20px_rgba(16,185,129,0.4)]'
                    : 'bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 shadow-[0_0_20px_rgba(0,240,255,0.4)]'
                } hover:scale-105`}
              >
                <Play className="w-4 h-4 fill-current" />
                <span>
                  {activeChallenge.status === 'RUNNING' ? 'WORKSPACE (LIVE)' : (activeChallenge.status === 'PAUSED' ? 'WORKSPACE (PAUSED)' : 'LAUNCH WORKSPACE')}
                </span>
              </button>
              <button
                onClick={() => { soundEngine.playAlarm(); onTriggerKillSwitch(); }}
                className="px-4 py-2.5 rounded-lg bg-red-950/80 hover:bg-red-900 border border-cyber-rose text-cyber-rose hover:text-white font-display font-bold text-xs flex items-center space-x-2 transition-all uppercase shadow-[0_0_15px_rgba(255,42,109,0.3)]"
              >
                <AlertOctagon className="w-4 h-4" />
                <span>STOP ENGINE</span>
              </button>
            </div>
          </div>

          {/* Operational Metrics Row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-5 text-xs">
            <div className="bg-obsidian-900/80 border border-slate-800 rounded-lg p-3.5 hover:border-cyber-cyan/40 transition-colors">
              <span className="text-slate-400 text-[10px] uppercase font-bold tracking-wider block mb-1">Target Mission</span>
              <span className="text-cyber-cyan font-semibold text-xs leading-snug block">
                {activeChallenge.description || 'Autonomous CTF Target Attack'}
              </span>
            </div>

            <div className="bg-obsidian-900/80 border border-slate-800 rounded-lg p-3.5 hover:border-cyber-emerald/40 transition-colors">
              <span className="text-slate-400 text-[10px] uppercase font-bold tracking-wider block mb-1">Active AI Brain</span>
              <span className="text-cyber-emerald font-semibold text-xs block">
                Gemini 1.5 Pro + DeepSeek R1
              </span>
            </div>

            <div className="bg-obsidian-900/80 border border-slate-800 rounded-lg p-3.5 hover:border-cyber-amber/40 transition-colors">
              <span className="text-slate-400 text-[10px] uppercase font-bold tracking-wider block mb-1">Status</span>
              <span className="text-cyber-amber font-semibold text-xs block">
                {activeChallenge.status}
              </span>
            </div>

            <div className="bg-obsidian-900/80 border border-slate-800 rounded-lg p-3.5 hover:border-cyber-violet/40 transition-colors">
              <span className="text-slate-400 text-[10px] uppercase font-bold tracking-wider block mb-1">Operation Duration</span>
              <div className="flex items-center space-x-1.5 text-slate-100 font-bold text-sm font-mono">
                <Clock className="w-4 h-4 text-cyber-cyan" />
                <span>{formatDuration(elapsedSeconds)}</span>
              </div>
            </div>
          </div>

          {/* Quick Action Cyber Bar */}
          <div className="mt-5 pt-4 border-t border-slate-800/80 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center space-x-2 text-[10px] text-slate-400 uppercase font-bold">
              <Zap className="w-3.5 h-3.5 text-cyber-cyan" />
              <span>QUICK OPERATIONS ARSENAL:</span>
            </div>
            <div className="flex items-center space-x-2">
              <button
                onClick={() => handleQuickAction('DEEP NMAP SCAN')}
                className="px-2.5 py-1 rounded bg-obsidian-900 border border-slate-700 hover:border-cyber-cyan text-slate-300 hover:text-cyber-cyan text-[11px] font-semibold transition-colors"
              >
                🔍 DEEP SCAN
              </button>
              <button
                onClick={() => handleQuickAction('FLAG SEARCH BURST')}
                className="px-2.5 py-1 rounded bg-obsidian-900 border border-slate-700 hover:border-cyber-emerald text-slate-300 hover:text-cyber-emerald text-[11px] font-semibold transition-colors"
              >
                🚀 FLAG BURST
              </button>
              <button
                onClick={() => handleQuickAction('PAYLOAD GENERATION')}
                className="px-2.5 py-1 rounded bg-obsidian-900 border border-slate-700 hover:border-cyber-amber text-slate-300 hover:text-cyber-amber text-[11px] font-semibold transition-colors"
              >
                ⚡ SYNTH PAYLOAD
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-8 shadow-[0_0_30px_rgba(0,240,255,0.15)] flex flex-col items-center justify-center text-center space-y-4 cyber-corner">
          <div className="w-14 h-14 rounded-xl bg-obsidian-900 border border-cyber-cyan flex items-center justify-center text-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.4)] animate-pulse">
            <Zap className="w-7 h-7" />
          </div>
          <div>
            <h1 className="text-2xl font-display font-bold tracking-wider text-slate-100 neon-text-cyan uppercase">
              NO ACTIVE OPERATION IN SYSTEM
            </h1>
            <p className="text-xs text-slate-400 max-w-lg mt-1 font-mono leading-relaxed">
              FORGE is online and standing by. Go to Challenges to initialize a new CTF challenge by entering the target IP, challenge name, and flag pattern.
            </p>
          </div>
        </div>
      )}

      {/* Grid Layout: Agent Overview + Target Radar Sweep */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 2. Agent Overview Panel (2 cols) */}
        <div className="lg:col-span-2 glass-panel border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center space-x-2">
              <Cpu className="w-5 h-5 text-cyber-cyan" />
              <h2 className="text-sm font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
                AGENT FLEET TELEMETRY
              </h2>
            </div>
            <span className="text-[10px] text-slate-400 font-bold bg-obsidian-900 px-2.5 py-1 rounded border border-slate-800">
              7 AGENTS IN FLEET
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {agents.map((ag) => (
              <div 
                key={ag.id} 
                className={`p-4 rounded-lg border text-xs transition-all hover:scale-[1.01] ${
                  ag.status === 'RUNNING' || ag.status === 'ANALYZING'
                    ? 'bg-obsidian-900/90 border-cyber-cyan/50 shadow-[0_0_15px_rgba(0,240,255,0.15)]'
                    : 'bg-obsidian-900/40 border-slate-800 text-slate-400'
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center space-x-2">
                    <span className={`w-2.5 h-2.5 rounded-full ${
                      ag.status === 'RUNNING' ? 'bg-cyber-emerald animate-ping' :
                      ag.status === 'ANALYZING' ? 'bg-cyber-cyan animate-ping' :
                      'bg-slate-600'
                    }`}></span>
                    <span className="font-bold text-slate-200 text-sm">{ag.name}</span>
                  </div>
                  <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase ${
                    ag.status === 'RUNNING' ? 'bg-emerald-950 text-cyber-emerald border border-emerald-800' :
                    ag.status === 'ANALYZING' ? 'bg-cyan-950 text-cyber-cyan border border-cyan-800' :
                    'bg-slate-900 text-slate-500 border border-slate-800'
                  }`}>
                    {ag.status}
                  </span>
                </div>

                <div className="space-y-2 text-[11px]">
                  <p className="text-slate-300 line-clamp-1">
                    <span className="text-slate-500 font-bold">TASK:</span> {ag.currentObjective}
                  </p>
                  
                  {/* Load / Activity Meter */}
                  <div className="space-y-1">
                    <div className="flex justify-between text-[10px] text-slate-400">
                      <span>WORKLOAD METER</span>
                      <span className="text-cyber-cyan font-bold">
                        {ag.status === 'RUNNING' ? '84%' : ag.status === 'ANALYZING' ? '62%' : '0%'}
                      </span>
                    </div>
                    <div className="w-full h-1.5 bg-obsidian-950 rounded-full overflow-hidden border border-slate-800">
                      <div 
                        className={`h-full transition-all duration-500 ${
                          ag.status === 'RUNNING' ? 'bg-cyber-emerald' :
                          ag.status === 'ANALYZING' ? 'bg-cyber-cyan' :
                          'bg-slate-700'
                        }`} 
                        style={{ width: ag.status === 'RUNNING' ? '84%' : ag.status === 'ANALYZING' ? '62%' : '0%' }}
                      ></div>
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-[10px] text-slate-400 pt-2 border-t border-slate-800/80">
                    <span>MODEL: <span className="text-cyber-cyan font-bold">{ag.selectedModel}</span></span>
                    <span>RUNTIME: {ag.runtime}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 4. Target Intelligence & Radar Graphic (1 col) */}
        <div className="glass-panel border border-slate-800 rounded-xl p-5 space-y-4 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-3">
              <div className="flex items-center space-x-2">
                <TargetIcon className="w-5 h-5 text-cyber-cyan" />
                <h2 className="text-sm font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
                  TARGET RADAR
                </h2>
              </div>
              <Radar className="w-4 h-4 text-cyber-cyan animate-spin" />
            </div>

            {/* Tactical Target Radar Graphic Component */}
            <div className="relative w-full h-40 bg-obsidian-950 rounded-lg border border-cyan-500/30 flex items-center justify-center overflow-hidden mb-4 shadow-inner">
              <div className="absolute inset-0 cyber-grid-bg opacity-30"></div>
              {/* Radar Rings */}
              <div className="w-32 h-32 rounded-full border border-cyber-cyan/30 flex items-center justify-center">
                <div className="w-20 h-20 rounded-full border border-cyber-cyan/40 flex items-center justify-center">
                  <div className="w-8 h-8 rounded-full border border-cyber-cyan/60 flex items-center justify-center">
                    <div className="w-2 h-2 rounded-full bg-cyber-rose animate-ping"></div>
                  </div>
                </div>
              </div>
              {/* Radar Sweep Line */}
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-36 h-36 border-r border-cyber-cyan/80 animate-radar-spin rounded-full origin-center"></div>
              </div>
              {/* Target Location Marker */}
              <div className="absolute top-8 right-12 flex items-center space-x-1 bg-obsidian-900/90 border border-cyber-rose/60 px-2 py-0.5 rounded text-[10px] text-cyber-rose font-bold">
                <span className="w-1.5 h-1.5 rounded-full bg-cyber-rose animate-ping"></span>
                <span>{target?.currentIp || 'NO TARGET'}</span>
              </div>
            </div>

            <div className="space-y-2 text-xs">
              <div className="flex justify-between border-b border-slate-800/60 pb-1.5">
                <span className="text-slate-400">Hostname:</span>
                <span className="text-slate-200 font-bold">{target?.hostname || 'N/A'}</span>
              </div>
              <div className="flex justify-between border-b border-slate-800/60 pb-1.5">
                <span className="text-slate-400">Discovery:</span>
                <span className="text-cyber-cyan text-[11px] font-semibold">{target?.discoveryMethod || 'N/A'}</span>
              </div>
              <div className="flex justify-between border-b border-slate-800/60 pb-1.5">
                <span className="text-slate-400">Last Probe:</span>
                <span className="text-cyber-emerald font-bold">{target?.lastVerified || 'Never'}</span>
              </div>

              <div className="pt-1">
                <span className="text-slate-400 text-[11px] block mb-1 font-bold">Open Ports & Services:</span>
                <div className="space-y-1">
                  {target?.services && target.services.length > 0 ? (
                    target.services.map((s) => (
                      <div key={s.port} className="flex items-center justify-between px-2.5 py-1 rounded bg-obsidian-950 border border-slate-800 text-[11px]">
                        <span className="text-cyber-cyan font-bold">{s.port}/{s.proto}</span>
                        <span className="text-slate-200 font-semibold">{s.service}</span>
                        <span className="text-slate-400 text-[10px]">{s.version}</span>
                      </div>
                    ))
                  ) : (
                    <div className="text-slate-500 text-[11px] py-1">No services discovered yet</div>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="pt-3 border-t border-slate-800">
            <button 
              onClick={() => { soundEngine.playClick(); if (activeChallenge) onOpenWorkspace(activeChallenge); }}
              disabled={!activeChallenge}
              className="w-full py-2 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-cyber-cyan/50 text-cyber-cyan text-xs font-bold flex items-center justify-center space-x-1 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>EXPLORE TARGET MATRIX</span>
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Grid Layout: Live Operations Feed + Provider Telemetry */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 3. Live Operations Feed (2 cols) */}
        <div className="lg:col-span-2 glass-panel border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center space-x-2">
              <TerminalIcon className="w-5 h-5 text-cyber-cyan" />
              <h2 className="text-sm font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
                REAL-TIME EXPLOIT TERMINAL STREAM
              </h2>
            </div>
            <div className="flex items-center space-x-2 text-[10px]">
              <Radio className="w-3.5 h-3.5 text-cyber-emerald animate-pulse" />
              <span className="text-cyber-emerald font-bold uppercase">LIVE FEED ACTIVE</span>
            </div>
          </div>

          <div className="bg-obsidian-950 border border-slate-800/90 rounded-lg p-4 font-mono text-xs space-y-2.5 max-h-60 overflow-y-auto shadow-inner">
            {liveEvents.map((evt, idx) => (
              <div key={idx} className="flex items-start space-x-3 py-1 border-b border-slate-900/80 last:border-0 hover:bg-obsidian-900/40 transition-colors">
                <span className="text-slate-500 text-[11px] shrink-0">{evt.time}</span>
                <span className="px-2 py-0.5 rounded bg-obsidian-900 text-slate-300 border border-slate-800 text-[10px] shrink-0 font-bold">
                  {evt.source}
                </span>
                <span className={`${evt.color} leading-relaxed`}>{evt.text}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 5. Provider Status Telemetry (1 col) */}
        <div className="glass-panel border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center space-x-2">
              <Server className="w-5 h-5 text-cyber-cyan" />
              <h2 className="text-sm font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
                LLM INFRASTRUCTURE
              </h2>
            </div>
            <span className="text-[10px] text-cyber-emerald font-bold px-2 py-0.5 rounded bg-emerald-950 border border-emerald-800">
              HEALTHY
            </span>
          </div>

          <div className="space-y-2.5">
            {providers.slice(0, 6).map((p) => (
              <div key={p.name} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-obsidian-900 border border-slate-800 text-xs hover:border-cyber-cyan/30 transition-colors">
                <div className="flex items-center space-x-2">
                  <span className="w-2 h-2 rounded-full bg-cyber-emerald animate-pulse"></span>
                  <span className="font-bold text-slate-200">{p.name}</span>
                </div>
                <div className="flex items-center space-x-3 text-[11px]">
                  <span className="text-slate-400 font-mono">{p.latency}</span>
                  <span className="px-2 py-0.5 rounded bg-emerald-950 text-cyber-emerald border border-emerald-800 text-[9px] font-bold">
                    READY
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

