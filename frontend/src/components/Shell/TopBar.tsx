import React, { useState, useEffect } from 'react';
import { 
  Wifi, 
  Users, 
  Cpu, 
  Clock, 
  CheckCircle2, 
  AlertTriangle 
} from 'lucide-react';
import { NavTab, Challenge } from '../../types';

interface TopBarProps {
  activeTab: NavTab;
  activeChallenge: Challenge | null;
  killSwitchActive: boolean;
}

export const TopBar: React.FC<TopBarProps> = ({
  activeTab,
  activeChallenge,
  killSwitchActive
}) => {
  const [timeStr, setTimeStr] = useState<string>('');

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(now.toISOString().substring(11, 19) + ' UTC');
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  const getSectionTitle = (): string => {
    if (activeChallenge) {
      return `CHALLENGE WORKSPACE: ${activeChallenge.name}`;
    }
    switch (activeTab) {
      case 'command': return 'COMMAND CENTER';
      case 'challenges': return 'CHALLENGES MANAGEMENT';
      case 'targets': return 'TARGET INTELLIGENCE';
      case 'agents': return 'AGENT TELEMETRY & CONTROL';
      case 'tools': return 'CAPABILITY TOOL MANAGER';
      case 'ai_intelligence': return 'AI OPERATIONS & ROUTING';
      case 'evidence': return 'EVIDENCE REPOSITORY';
      case 'terminal': return 'INTEGRATED OPERATIONAL TERMINAL';
      case 'providers': return 'AI INFRASTRUCTURE PROVIDERS';
      case 'system': return 'SYSTEM ADMINISTRATION & CHECKPOINTS';
      default: return 'COMMAND CENTER';
    }
  };

  return (
    <header className="h-13 border-b border-slate-800/80 bg-[#090d14]/90 backdrop-blur-md px-5 flex items-center justify-between font-mono z-10 select-none">
      {/* Left: Current Section Title */}
      <div className="flex items-center space-x-3">
        <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">
          {getSectionTitle()}
        </h2>
      </div>

      {/* Center: Current Active Operation Context Banner */}
      <div className="hidden md:flex items-center space-x-2 px-3 py-1 rounded bg-cyan-950/40 border border-cyan-500/30 text-cyan-300 text-xs font-semibold tracking-wider">
        <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
        <span>
          {activeChallenge
            ? `${activeChallenge.name} • ${activeChallenge.category} • ${activeChallenge.target}`
            : 'VAULT • WEB • 10.10.14.23'}
        </span>
      </div>

      {/* Right: Operational Telemetry Pills */}
      <div className="flex items-center space-x-3 text-[11px]">
        {/* Network Status */}
        <div className="hidden lg:flex items-center space-x-1.5 px-2.5 py-1 rounded bg-slate-900/80 border border-slate-800 text-slate-300">
          <Wifi className="w-3 h-3 text-cyan-400" />
          <span className="text-slate-400">NET:</span>
          <span className="text-slate-200">1 Gbps</span>
        </div>

        {/* Agent Count */}
        <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-slate-900/80 border border-slate-800 text-slate-300">
          <Users className="w-3 h-3 text-emerald-400" />
          <span className="text-slate-400">AGENTS:</span>
          <span className="text-emerald-400 font-semibold">3 ACTIVE</span>
          <span className="text-slate-400">/ 7</span>
        </div>

        {/* Provider Status */}
        <div className="hidden sm:flex items-center space-x-1.5 px-2.5 py-1 rounded bg-slate-900/80 border border-slate-800 text-slate-300">
          <Cpu className="w-3 h-3 text-cyan-400" />
          <span className="text-slate-400">LLM:</span>
          <span className="text-cyan-300 font-semibold">GEMINI</span>
          <span className="text-emerald-400 text-[9px] uppercase">● ONLINE</span>
        </div>

        {/* UTC Live Clock */}
        <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-slate-900/80 border border-slate-800 text-slate-200">
          <Clock className="w-3 h-3 text-slate-400" />
          <span className="text-cyan-400 font-bold">{timeStr || '18:42:05 UTC'}</span>
        </div>

        {/* System Status */}
        <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-slate-900/80 border border-slate-800">
          {killSwitchActive ? (
            <>
              <AlertTriangle className="w-3 h-3 text-red-400" />
              <span className="text-red-400 font-bold">HALTED</span>
            </>
          ) : (
            <>
              <CheckCircle2 className="w-3 h-3 text-emerald-400" />
              <span className="text-emerald-400 font-bold">SYS OK</span>
            </>
          )}
        </div>
      </div>
    </header>
  );
};
