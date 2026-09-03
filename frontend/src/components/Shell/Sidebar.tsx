import React, { useState } from 'react';
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
  Wifi,
  Volume2,
  VolumeX,
  PanelLeftClose,
  PanelLeftOpen
} from 'lucide-react';
import { NavTab } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

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
  const [muted, setMuted] = useState(soundEngine.isMuted());
  const [isCollapsed, setIsCollapsed] = useState(false);

  const handleNav = (tab: NavTab) => {
    soundEngine.playTabSwitch();
    if (activeChallengeId) {
      onClearActiveChallenge();
    }
    setActiveTab(tab);
  };

  const toggleAudio = () => {
    const isMuted = soundEngine.toggleMute();
    setMuted(isMuted);
  };

  const handleKillSwitch = () => {
    soundEngine.playAlarm();
    onTriggerKillSwitch();
  };

  return (
    <aside className={`${isCollapsed ? 'w-20' : 'w-68'} border-r border-cyan-500/20 bg-obsidian-950/95 flex flex-col justify-between select-none z-20 backdrop-blur-xl relative transition-all duration-300`}>
      {/* Subtle Grid overlay */}
      <div className="absolute inset-0 cyber-grid-bg opacity-40 pointer-events-none"></div>

      {/* Top Branding Header */}
      <div className="relative z-10">
        <div className={`p-4 border-b border-cyan-500/20 flex items-center ${isCollapsed ? 'justify-center flex-col space-y-3' : 'justify-between'} bg-obsidian-900/60 transition-all`}>
          <div className="flex items-center space-x-3 cursor-pointer" onClick={() => handleNav('command')}>
            <div className="relative flex items-center justify-center w-10 h-10 rounded-lg bg-obsidian-900 border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.3)] group hover:scale-105 transition-transform shrink-0">
              <Shield className="w-5 h-5 text-cyber-cyan group-hover:rotate-12 transition-transform" />
              <div className="absolute inset-0 bg-cyber-cyan/10 animate-pulse rounded-lg"></div>
            </div>
            {!isCollapsed && (
              <div>
                <div className="flex items-center space-x-1.5">
                  <span className="font-display font-bold tracking-widest text-slate-100 text-lg neon-text-cyan">FORGE</span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-950/80 border border-cyber-cyan/50 text-cyber-cyan font-bold tracking-wider">v2.0</span>
                </div>
                <p className="text-[9px] text-slate-400 font-mono tracking-widest uppercase">Autonomous Exploitation</p>
              </div>
            )}
          </div>

          <div className="flex items-center space-x-1.5">
            {/* Collapse / Expand Toggle Button */}
            <button
              onClick={() => { soundEngine.playClick(); setIsCollapsed(!isCollapsed); }}
              title={isCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
              className="p-2 rounded-lg border border-slate-800 hover:border-cyber-cyan/50 bg-obsidian-800 text-slate-400 hover:text-cyber-cyan transition-colors"
            >
              {isCollapsed ? <PanelLeftOpen className="w-4 h-4 text-cyber-cyan" /> : <PanelLeftClose className="w-4 h-4 text-slate-400" />}
            </button>

            {/* Sound FX Toggle */}
            {!isCollapsed && (
              <button 
                onClick={toggleAudio}
                title={muted ? "Unmute Audio FX" : "Mute Audio FX"}
                className="p-2 rounded-lg border border-slate-800 hover:border-cyber-cyan/50 bg-obsidian-800 text-slate-400 hover:text-cyber-cyan transition-colors"
              >
                {muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4 text-cyber-cyan" />}
              </button>
            )}
          </div>
        </div>

        {/* Navigation Sections */}
        <nav className="p-3.5 space-y-4 overflow-y-auto max-h-[calc(100vh-230px)] font-mono relative z-10">
          {/* COMMAND SECTION */}
          <div>
            {!isCollapsed && (
              <div className="text-[9px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-1.5 flex items-center justify-between">
                <span>COMMAND</span>
                <span className="w-1.5 h-1.5 rounded-full bg-cyber-cyan/60"></span>
              </div>
            )}
            <button
              onClick={() => handleNav('command')}
              title="Command Center"
              className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all relative ${
                activeTab === 'command' && !activeChallengeId
                  ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
              }`}
            >
              <Activity className="w-4 h-4 shrink-0" />
              {!isCollapsed && <span>Command Center</span>}
              {activeTab === 'command' && !activeChallengeId && !isCollapsed && (
                <span className="absolute right-2 w-1.5 h-1.5 rounded-full bg-cyber-cyan animate-ping"></span>
              )}
            </button>
          </div>

          {/* OPERATIONS SECTION */}
          <div>
            {!isCollapsed && (
              <div className="text-[9px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-1.5 flex items-center justify-between">
                <span>OPERATIONS</span>
                <span className="w-1.5 h-1.5 rounded-full bg-cyber-emerald/60"></span>
              </div>
            )}
            <div className="space-y-1">
              <button
                onClick={() => handleNav('challenges')}
                title="Challenges"
                className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                  (activeTab === 'challenges' || activeChallengeId)
                    ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Shield className="w-4 h-4 shrink-0" />
                {!isCollapsed && <span>Challenges</span>}
              </button>
              <button
                onClick={() => handleNav('targets')}
                title="Targets"
                className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                  activeTab === 'targets' && !activeChallengeId
                    ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <TargetIcon className="w-4 h-4 shrink-0" />
                {!isCollapsed && <span>Targets</span>}
              </button>
            </div>
          </div>

          {/* INTELLIGENCE SECTION */}
          <div>
            {!isCollapsed && (
              <div className="text-[9px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-1.5 flex items-center justify-between">
                <span>INTELLIGENCE</span>
                <span className="w-1.5 h-1.5 rounded-full bg-cyber-violet/60"></span>
              </div>
            )}
            <div className="space-y-1">
              <button
                onClick={() => handleNav('ai_intelligence')}
                title="AI Intelligence"
                className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                  activeTab === 'ai_intelligence' && !activeChallengeId
                    ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Cpu className="w-4 h-4 shrink-0" />
                {!isCollapsed && <span>AI Intelligence</span>}
              </button>
              <button
                onClick={() => handleNav('evidence')}
                title="Evidence Vault"
                className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                  activeTab === 'evidence' && !activeChallengeId
                    ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <FileText className="w-4 h-4 shrink-0" />
                {!isCollapsed && <span>Evidence Vault</span>}
              </button>
            </div>
          </div>

          {/* EXECUTION SECTION */}
          <div>
            {!isCollapsed && (
              <div className="text-[9px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-1.5 flex items-center justify-between">
                <span>EXECUTION</span>
                <span className="w-1.5 h-1.5 rounded-full bg-cyber-amber/60"></span>
              </div>
            )}
            <div className="space-y-1">
              <button
                onClick={() => handleNav('terminal')}
                title="Terminal Stream"
                className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                  activeTab === 'terminal' && !activeChallengeId
                    ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <TerminalIcon className="w-4 h-4 shrink-0" />
                {!isCollapsed && <span>Terminal Stream</span>}
              </button>
              <button
                onClick={() => handleNav('tools')}
                title="Tool Arsenal"
                className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                  activeTab === 'tools' && !activeChallengeId
                    ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Wrench className="w-4 h-4 shrink-0" />
                {!isCollapsed && <span>Tool Arsenal</span>}
              </button>
              <button
                onClick={() => handleNav('agents')}
                title="Agent Fleet"
                className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                  activeTab === 'agents' && !activeChallengeId
                    ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
                }`}
              >
                <Users className="w-4 h-4 shrink-0" />
                {!isCollapsed && <span>Agent Fleet</span>}
              </button>
            </div>
          </div>

          {/* AI INFRASTRUCTURE SECTION */}
          <div>
            {!isCollapsed && (
              <div className="text-[9px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-1.5 flex items-center justify-between">
                <span>INFRASTRUCTURE</span>
              </div>
            )}
            <button
              onClick={() => handleNav('providers')}
              title="LLM Providers"
              className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                activeTab === 'providers' && !activeChallengeId
                  ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
              }`}
            >
              <Server className="w-4 h-4 shrink-0" />
              {!isCollapsed && <span>LLM Providers</span>}
            </button>
          </div>

          {/* SYSTEM SECTION */}
          <div>
            {!isCollapsed && (
              <div className="text-[9px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-1.5 flex items-center justify-between">
                <span>SYSTEM</span>
              </div>
            )}
            <button
              onClick={() => handleNav('system')}
              title="System Telemetry"
              className={`w-full flex items-center ${isCollapsed ? 'justify-center' : 'space-x-2.5 px-3'} py-2 rounded text-xs transition-all ${
                activeTab === 'system' && !activeChallengeId
                  ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_15px_rgba(0,240,255,0.2)] font-bold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60 border border-transparent'
              }`}
            >
              <Settings className="w-4 h-4 shrink-0" />
              {!isCollapsed && <span>System Telemetry</span>}
            </button>
          </div>
        </nav>
      </div>

      {/* Bottom Telemetry Widget & Emergency Stop Button */}
      <div className="p-3 border-t border-cyan-500/20 bg-obsidian-900/90 space-y-3 font-mono text-[11px] relative z-10">
        {!isCollapsed && (
          <div className="space-y-1.5 px-1 bg-obsidian-950/60 p-2 rounded border border-slate-800">
            <div className="flex items-center justify-between text-slate-300">
              <span className="text-slate-400 flex items-center space-x-1.5 text-[10px]">
                <Wifi className="w-3 h-3 text-cyber-cyan" />
                <span>COMM LINK</span>
              </span>
              <span className="text-cyber-emerald font-bold text-[10px] flex items-center space-x-1">
                <span className="w-1.5 h-1.5 rounded-full bg-cyber-emerald animate-pulse"></span>
                <span>ONLINE</span>
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-300">
              <span className="text-slate-400 flex items-center space-x-1.5 text-[10px]">
                <Activity className="w-3 h-3 text-cyber-cyan" />
                <span>CORE MATRIX</span>
              </span>
              <span className={`font-bold text-[10px] flex items-center space-x-1 ${killSwitchActive ? 'text-cyber-rose' : 'text-cyber-emerald'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${killSwitchActive ? 'bg-cyber-rose' : 'bg-cyber-emerald animate-pulse'}`}></span>
                <span>{killSwitchActive ? 'LOCKDOWN' : 'ACTIVE'}</span>
              </span>
            </div>
          </div>
        )}

        {/* Global Emergency Stop Button */}
        <button
          onClick={handleKillSwitch}
          title="[ EMERGENCY STOP ]"
          className={`w-full py-2.5 ${isCollapsed ? 'px-1' : 'px-3'} rounded font-display font-bold text-[11px] tracking-widest flex items-center justify-center space-x-2 transition-all duration-300 uppercase shadow-lg ${
            killSwitchActive
              ? 'bg-cyber-rose text-white border border-rose-400 shadow-[0_0_20px_rgba(255,42,109,0.8)] animate-bounce'
              : 'bg-red-950/60 hover:bg-red-900/90 border border-cyber-rose/60 text-cyber-rose hover:text-white shadow-[0_0_12px_rgba(255,42,109,0.25)]'
          }`}
        >
          <AlertOctagon className="w-4 h-4 shrink-0" />
          {!isCollapsed && <span>[ EMERGENCY STOP ]</span>}
        </button>
      </div>
    </aside>
  );
};

