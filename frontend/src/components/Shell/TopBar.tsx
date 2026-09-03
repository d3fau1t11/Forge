import React, { useState, useEffect } from 'react';
import { 
  Clock, 
  CheckCircle2, 
  AlertTriangle,
  Zap,
  SlidersHorizontal
} from 'lucide-react';
import { NavTab, Challenge } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

interface TopBarProps {
  activeTab: NavTab;
  activeChallenge: Challenge | null;
  killSwitchActive: boolean;
  operationalMode: string;
  onModeChange: (mode: string) => void;
}

export const TopBar: React.FC<TopBarProps> = ({
  activeTab,
  activeChallenge,
  killSwitchActive,
  operationalMode,
  onModeChange
}) => {
  const [timeStr, setTimeStr] = useState<string>('');
  const [useLocalTime, setUseLocalTime] = useState<boolean>(false);

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      if (useLocalTime) {
        setTimeStr(now.toLocaleTimeString());
      } else {
        setTimeStr(now.toISOString().substring(11, 19) + ' UTC');
      }
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, [useLocalTime]);

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
    <header className="h-14 border-b border-cyber-cyan/20 bg-obsidian-950/90 backdrop-blur-md px-5 flex items-center justify-between font-mono z-10 select-none relative">
      {/* Left: Section Header & Operational Mode Dropdown */}
      <div className="flex items-center space-x-4">
        <div className="flex items-center space-x-2">
          <div className="w-2 h-2 rounded-full bg-cyber-cyan shadow-[0_0_8px_#00f0ff] animate-ping"></div>
          <h2 className="text-sm font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
            {getSectionTitle()}
          </h2>
        </div>

      {/* Operational Strategy Mode Selector */}
        <div className="flex items-center space-x-2 bg-obsidian-900 border border-slate-800 rounded-lg px-3 py-1 text-xs">
          <SlidersHorizontal className="w-3.5 h-3.5 text-cyber-cyan" />
          <span className="text-[10px] text-slate-400 font-bold uppercase">MODE:</span>
          <select 
            value={operationalMode}
            onChange={(e) => {
              soundEngine.playClick();
              onModeChange(e.target.value);
            }}
            className="bg-transparent text-slate-200 font-mono text-xs focus:outline-none cursor-pointer text-cyber-cyan font-bold"
          >
            <option value="CTF_OFFENSIVE_CONTROLLED" className="bg-obsidian-950 text-cyber-cyan">⚡ CTF CONTROLLED</option>
            <option value="FULL_AUTONOMOUS_EXPLOITATION" className="bg-obsidian-950 text-cyber-rose">🔥 FULL AUTONOMOUS</option>
            <option value="STEALTH_RECON" className="bg-obsidian-950 text-emerald-400">🛡️ STEALTH RECON</option>
          </select>
        </div>
      </div>

      {/* Right: Essential Telemetry Controls */}
      <div className="flex items-center space-x-3 text-xs font-mono">
        {/* Active Challenge Badge */}
        {activeChallenge && (
          <div className="hidden sm:flex items-center space-x-1.5 px-3 py-1 rounded-lg bg-obsidian-900 border border-cyber-cyan/40 text-cyber-cyan">
            <Zap className="w-3.5 h-3.5 text-cyber-cyan animate-pulse" />
            <span className="font-bold truncate max-w-[140px] uppercase">{activeChallenge.name}</span>
          </div>
        )}

        {/* UTC / Local Clock */}
        <button 
          onClick={() => { soundEngine.playClick(); setUseLocalTime(!useLocalTime); }}
          title="Click to toggle UTC / Local Time"
          className="flex items-center space-x-1.5 px-3 py-1 rounded-lg bg-obsidian-900 border border-slate-800 hover:border-cyber-cyan/50 text-slate-200 transition-colors"
        >
          <Clock className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-cyber-cyan font-bold font-mono">{timeStr || '18:42:05 UTC'}</span>
        </button>

        {/* System Health Status */}
        <div className="flex items-center space-x-1.5 px-3 py-1 rounded-lg bg-obsidian-900 border border-slate-800">
          {killSwitchActive ? (
            <>
              <AlertTriangle className="w-3.5 h-3.5 text-cyber-rose animate-bounce" />
              <span className="text-cyber-rose font-bold text-xs">LOCKDOWN</span>
            </>
          ) : (
            <>
              <CheckCircle2 className="w-3.5 h-3.5 text-cyber-emerald" />
              <span className="text-cyber-emerald font-bold text-xs">SYS OK</span>
            </>
          )}
        </div>
      </div>
    </header>
  );
};

