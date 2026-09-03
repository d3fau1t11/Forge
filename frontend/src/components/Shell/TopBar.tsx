import React, { useState, useEffect } from 'react';
import { 
  Users, 
  Cpu, 
  Clock, 
  CheckCircle2, 
  AlertTriangle,
  Zap,
  SlidersHorizontal,
  Radio
} from 'lucide-react';
import { NavTab, Challenge } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

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
  const [useLocalTime, setUseLocalTime] = useState<boolean>(false);
  const [operationalMode, setOperationalMode] = useState<'stealth' | 'balanced' | 'aggressive'>('balanced');

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

  const handleModeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    soundEngine.playClick();
    setOperationalMode(e.target.value as any);
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
        <div className="hidden xl:flex items-center space-x-2 bg-obsidian-900 border border-slate-800 rounded px-2 py-1 text-xs">
          <SlidersHorizontal className="w-3.5 h-3.5 text-cyber-cyan" />
          <span className="text-[10px] text-slate-400 font-bold uppercase">MODE:</span>
          <select 
            value={operationalMode}
            onChange={handleModeChange}
            className="bg-transparent text-slate-200 font-mono text-xs focus:outline-none cursor-pointer text-cyber-cyan font-bold"
          >
            <option value="stealth" className="bg-obsidian-950 text-slate-200">🛡️ STEALTH RECON</option>
            <option value="balanced" className="bg-obsidian-950 text-cyber-cyan">⚡ BALANCED ATTACK</option>
            <option value="aggressive" className="bg-obsidian-950 text-cyber-rose">🔥 AGGRESSIVE FLAG RUSH</option>
          </select>
        </div>
      </div>

      {/* Center: Live Threat & Exploitation Stream Marquee Ticker */}
      <div className="hidden lg:flex flex-1 max-w-xl mx-4 items-center bg-obsidian-900/80 border border-slate-800/80 rounded px-3 py-1 text-xs overflow-hidden relative">
        <div className="flex items-center space-x-1.5 text-cyber-amber pr-2 border-r border-slate-800 whitespace-nowrap z-10 bg-obsidian-900 font-bold text-[10px]">
          <Radio className="w-3 h-3 animate-pulse" />
          <span>INTEL FEED:</span>
        </div>
        <div className="overflow-hidden whitespace-nowrap w-full pl-2">
          <div className="inline-block animate-marquee text-[11px] text-slate-300 space-x-6">
            <span className="text-cyber-emerald font-semibold">● [AUTONOMOUS SCAN] Active Nmap port probe on target 10.10.14.23:8080 (Apache HTTPd)</span>
            <span className="text-cyber-cyan font-semibold">● [AI ROUTE] Gemini 1.5 Flash selected for web payload synthesis (confidence 98.4%)</span>
            <span className="text-cyber-rose font-semibold">● [ALERT] SQL Injection endpoint identified at /api/v1/auth/login</span>
            <span className="text-cyber-amber font-semibold">● [EVIDENCE] Encrypted JWT token captured and logged to vault</span>
          </div>
        </div>
      </div>

      {/* Right: Telemetry Pills */}
      <div className="flex items-center space-x-2 text-[11px]">
        {/* Active Operation Badge */}
        <div className="hidden sm:flex items-center space-x-1.5 px-2.5 py-1 rounded bg-obsidian-900 border border-cyber-cyan/30 text-cyber-cyan text-xs">
          <Zap className="w-3 h-3 text-cyber-cyan animate-pulse" />
          <span className="font-semibold truncate max-w-[120px]">
            {activeChallenge ? activeChallenge.name : 'VAULT CTF'}
          </span>
        </div>

        {/* Agent Count */}
        <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-obsidian-900 border border-slate-800 text-slate-300">
          <Users className="w-3 h-3 text-cyber-emerald" />
          <span className="text-slate-400 hidden md:inline">AGENTS:</span>
          <span className="text-cyber-emerald font-bold">3 ACTIVE</span>
        </div>

        {/* LLM Provider Status */}
        <div className="hidden md:flex items-center space-x-1.5 px-2.5 py-1 rounded bg-obsidian-900 border border-slate-800 text-slate-300">
          <Cpu className="w-3 h-3 text-cyber-cyan" />
          <span className="text-slate-400">LLM:</span>
          <span className="text-cyber-cyan font-bold">GEMINI</span>
          <span className="w-1.5 h-1.5 rounded-full bg-cyber-emerald animate-ping"></span>
        </div>

        {/* UTC/Local Toggle Cyber Clock */}
        <button 
          onClick={() => { soundEngine.playClick(); setUseLocalTime(!useLocalTime); }}
          title="Click to toggle UTC / Local Time"
          className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-obsidian-900 border border-slate-800 hover:border-cyber-cyan/50 text-slate-200 transition-colors"
        >
          <Clock className="w-3 h-3 text-slate-400" />
          <span className="text-cyber-cyan font-bold font-mono">{timeStr || '18:42:05 UTC'}</span>
        </button>

        {/* System Health Status */}
        <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-obsidian-900 border border-slate-800">
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

