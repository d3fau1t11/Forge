import React from 'react';
import { 
  Shield, 
  Target as TargetIcon, 
  Cpu, 
  FileText, 
  Terminal as TerminalIcon, 
  Wrench, 
  Users, 
  Server, 
  Settings, 
  AlertOctagon,
  Activity,
  Wifi
} from 'lucide-react';
import { NavTab } from '../../types';

interface SidebarProps {
  activeTab: NavTab;
  setActiveTab: (tab: NavTab) => void;
  activeChallengeId: string | null;
  onClearActiveChallenge: () => void;
  onTriggerKillSwitch: () => void;
  killSwitchActive: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  activeChallengeId,
  onClearActiveChallenge,
  onTriggerKillSwitch,
  killSwitchActive
}) => {
  const handleNav = (tab: NavTab) => {
    if (activeChallengeId) {
      onClearActiveChallenge();
    }
    setActiveTab(tab);
  };

  return (
    <aside className="w-64 border-r border-slate-800/80 bg-[#090d14] flex flex-col justify-between select-none z-20">
      {/* Top Branding Header */}
      <div>
        <div className="p-4 border-b border-slate-800/80 flex items-center justify-between">
          <div className="flex items-center space-x-3 cursor-pointer" onClick={() => handleNav('command')}>
            <div className="relative flex items-center justify-center w-9 h-9 rounded bg-cyan-950/60 border border-cyan-500/50 shadow-[0_0_12px_rgba(6,182,212,0.25)]">
              <Shield className="w-5 h-5 text-cyan-400" />
              <div className="absolute inset-0 bg-cyan-400/10 animate-pulse rounded"></div>
            </div>
            <div>
              <div className="flex items-center space-x-1.5">
                <span className="font-mono font-bold tracking-widest text-slate-100 text-base">FORGE</span>
                <span className="text-[10px] font-mono px-1 py-0.2 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 font-semibold">v2.0</span>
              </div>
              <p className="text-[10px] text-slate-400 font-mono tracking-tight uppercase">Autonomous CTF Command</p>
            </div>
          </div>
        </div>

        {/* Navigation Sections */}
        <nav className="p-3 space-y-4 overflow-y-auto max-h-[calc(100vh-220px)] font-mono">
          {/* COMMAND SECTION */}
          <div>
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest px-2 mb-1">
              COMMAND
            </div>
            <button
              onClick={() => handleNav('command')}
              className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                activeTab === 'command' && !activeChallengeId
                  ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
              }`}
            >
              <Activity className="w-3.5 h-3.5" />
              <span>Command Center</span>
            </button>
          </div>

          {/* OPERATIONS SECTION */}
          <div>
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest px-2 mb-1">
              OPERATIONS
            </div>
            <div className="space-y-0.5">
              <button
                onClick={() => handleNav('challenges')}
                className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                  (activeTab === 'challenges' || activeChallengeId)
                    ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Shield className="w-3.5 h-3.5" />
                <span>Challenges</span>
              </button>
              <button
                onClick={() => handleNav('targets')}
                className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                  activeTab === 'targets' && !activeChallengeId
                    ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <TargetIcon className="w-3.5 h-3.5" />
                <span>Targets</span>
              </button>
            </div>
          </div>

          {/* INTELLIGENCE SECTION */}
          <div>
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest px-2 mb-1">
              INTELLIGENCE
            </div>
            <div className="space-y-0.5">
              <button
                onClick={() => handleNav('ai_intelligence')}
                className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                  activeTab === 'ai_intelligence' && !activeChallengeId
                    ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Cpu className="w-3.5 h-3.5" />
                <span>AI Intelligence</span>
              </button>
              <button
                onClick={() => handleNav('evidence')}
                className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                  activeTab === 'evidence' && !activeChallengeId
                    ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <FileText className="w-3.5 h-3.5" />
                <span>Evidence</span>
              </button>
            </div>
          </div>

          {/* EXECUTION SECTION */}
          <div>
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest px-2 mb-1">
              EXECUTION
            </div>
            <div className="space-y-0.5">
              <button
                onClick={() => handleNav('terminal')}
                className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                  activeTab === 'terminal' && !activeChallengeId
                    ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <TerminalIcon className="w-3.5 h-3.5" />
                <span>Terminal</span>
              </button>
              <button
                onClick={() => handleNav('tools')}
                className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                  activeTab === 'tools' && !activeChallengeId
                    ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Wrench className="w-3.5 h-3.5" />
                <span>Tools</span>
              </button>
              <button
                onClick={() => handleNav('agents')}
                className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                  activeTab === 'agents' && !activeChallengeId
                    ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Users className="w-3.5 h-3.5" />
                <span>Agents</span>
              </button>
            </div>
          </div>

          {/* AI INFRASTRUCTURE SECTION */}
          <div>
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest px-2 mb-1">
              AI INFRASTRUCTURE
            </div>
            <button
              onClick={() => handleNav('providers')}
              className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                activeTab === 'providers' && !activeChallengeId
                  ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
              }`}
            >
              <Server className="w-3.5 h-3.5" />
              <span>Providers</span>
            </button>
          </div>

          {/* SYSTEM SECTION */}
          <div>
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest px-2 mb-1">
              SYSTEM
            </div>
            <button
              onClick={() => handleNav('system')}
              className={`w-full flex items-center space-x-2.5 px-2.5 py-1.5 rounded text-xs transition-all ${
                activeTab === 'system' && !activeChallengeId
                  ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)] font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
              }`}
            >
              <Settings className="w-3.5 h-3.5" />
              <span>System</span>
            </button>
          </div>
        </nav>
      </div>

      {/* Bottom Operational Telemetry & Emergency Stop */}
      <div className="p-3 border-t border-slate-800/80 bg-[#070a0f] space-y-2.5 font-mono text-[11px]">
        <div className="space-y-1 px-1">
          <div className="flex items-center justify-between text-slate-300">
            <span className="text-slate-400 flex items-center space-x-1.5">
              <Wifi className="w-3 h-3 text-cyan-400" />
              <span>NETWORK</span>
            </span>
            <span className="text-emerald-400 font-semibold flex items-center space-x-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              <span>CONNECTED</span>
            </span>
          </div>
          <div className="flex items-center justify-between text-slate-300">
            <span className="text-slate-400 flex items-center space-x-1.5">
              <Activity className="w-3 h-3 text-cyan-400" />
              <span>FORGE</span>
            </span>
            <span className={`font-semibold flex items-center space-x-1 ${killSwitchActive ? 'text-red-400' : 'text-emerald-400'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${killSwitchActive ? 'bg-red-500' : 'bg-emerald-400 animate-pulse'}`}></span>
              <span>{killSwitchActive ? 'HALTED' : 'OPERATIONAL'}</span>
            </span>
          </div>
        </div>

        {/* Global Emergency Stop Button */}
        <button
          onClick={onTriggerKillSwitch}
          className={`w-full py-2 px-3 rounded font-mono font-bold text-[11px] tracking-wider flex items-center justify-center space-x-1.5 transition-all duration-200 uppercase ${
            killSwitchActive
              ? 'bg-red-950/80 border border-red-500 text-red-400 shadow-[0_0_15px_rgba(239,68,68,0.3)] animate-pulse'
              : 'bg-red-900/40 hover:bg-red-800/70 border border-red-600/80 text-red-300 shadow-[0_0_10px_rgba(220,38,38,0.2)] hover:text-white'
          }`}
        >
          <AlertOctagon className="w-3.5 h-3.5" />
          <span>[ EMERGENCY STOP ]</span>
        </button>
      </div>
    </aside>
  );
};
