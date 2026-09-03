import React, { useEffect } from 'react';
import { AlertOctagon, ShieldAlert, Play, Database, FileText, Lock } from 'lucide-react';
import { soundEngine } from '../../utils/soundEngine';

interface EmergencyStopModalProps {
  isOpen: boolean;
  onResume: () => void;
}

export const EmergencyStopModal: React.FC<EmergencyStopModalProps> = ({
  isOpen,
  onResume
}) => {
  useEffect(() => {
    if (isOpen) {
      soundEngine.playAlarm();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleResume = () => {
    soundEngine.playSuccess();
    onResume();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-xl p-4 select-none animate-fadeIn">
      <div className="w-full max-w-2xl bg-obsidian-950 border-2 border-cyber-rose/80 rounded-xl p-6 shadow-[0_0_80px_rgba(255,42,109,0.5)] font-mono text-slate-100 relative cyber-corner animate-strobe-red">
        
        {/* Top Warning Banner */}
        <div className="flex items-center justify-between border-b border-cyber-rose/50 pb-4 mb-5 bg-obsidian-900/80 p-3 rounded">
          <div className="flex items-center space-x-3">
            <div className="w-12 h-12 rounded-lg bg-cyber-rose/20 border border-cyber-rose flex items-center justify-center text-cyber-rose shadow-[0_0_20px_rgba(255,42,109,0.6)] animate-pulse">
              <AlertOctagon className="w-8 h-8" />
            </div>
            <div>
              <h1 className="text-2xl font-display font-bold tracking-widest text-cyber-rose neon-text-rose">
                EMERGENCY LOCKDOWN ACTIVATED
              </h1>
              <p className="text-xs text-rose-300 font-bold tracking-wider uppercase flex items-center space-x-2">
                <span className="w-2 h-2 rounded-full bg-cyber-rose animate-ping"></span>
                <span>SYSTEM EXPLOITATION PAUSED BY OPERATOR KILL SWITCH</span>
              </p>
            </div>
          </div>

          <div className="hidden sm:flex flex-col items-end">
            <span className="text-[10px] text-slate-400">SAFETY STATUS</span>
            <span className="text-xs font-bold text-cyber-rose flex items-center space-x-1">
              <Lock className="w-3.5 h-3.5" />
              <span>ISOLATED</span>
            </span>
          </div>
        </div>

        {/* Stopped Components Section */}
        <div className="space-y-4 text-xs">
          <div className="bg-obsidian-900/90 border border-cyber-rose/40 rounded-lg p-4 space-y-3 shadow-inner">
            <div className="flex items-center space-x-2 text-cyber-rose font-bold uppercase text-xs tracking-wider">
              <ShieldAlert className="w-4 h-4 text-cyber-rose" />
              <span>FORGE Autonomous Engine State:</span>
            </div>
            <ul className="grid grid-cols-2 gap-2.5 text-slate-300 font-mono text-[11px] pt-1">
              <li className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded border border-rose-950">
                <span className="w-2 h-2 rounded-full bg-cyber-rose animate-pulse"></span>
                <span>AI Agent Reasoning: <strong className="text-cyber-rose">HALTED</strong></span>
              </li>
              <li className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded border border-rose-950">
                <span className="w-2 h-2 rounded-full bg-cyber-rose animate-pulse"></span>
                <span>Subprocess & Tool Exec: <strong className="text-cyber-rose">HALTED</strong></span>
              </li>
              <li className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded border border-rose-950">
                <span className="w-2 h-2 rounded-full bg-cyber-rose animate-pulse"></span>
                <span>LLM Payload Calls: <strong className="text-cyber-rose">HALTED</strong></span>
              </li>
              <li className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded border border-rose-950">
                <span className="w-2 h-2 rounded-full bg-cyber-rose animate-pulse"></span>
                <span>Headless Browser: <strong className="text-cyber-rose">HALTED</strong></span>
              </li>
            </ul>
          </div>

          {/* Preserved Data Notice */}
          <div className="bg-obsidian-900/60 border border-slate-800 rounded-lg p-4 space-y-2">
            <div className="flex items-center space-x-2 text-cyber-emerald font-bold uppercase text-xs tracking-wider">
              <Database className="w-4 h-4" />
              <span>Telemetry & Findings Preserved Intact:</span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-slate-300 text-[11px]">
              <div className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded">
                <FileText className="w-3.5 h-3.5 text-cyber-cyan" />
                <span>Captured Flags & Evidence</span>
              </div>
              <div className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded">
                <FileText className="w-3.5 h-3.5 text-cyber-cyan" />
                <span>Terminal Log History</span>
              </div>
              <div className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded">
                <FileText className="w-3.5 h-3.5 text-cyber-cyan" />
                <span>Checkpoint State Snapshot</span>
              </div>
              <div className="flex items-center space-x-2 bg-obsidian-950 p-2 rounded">
                <FileText className="w-3.5 h-3.5 text-cyber-cyan" />
                <span>Target & Port Profiles</span>
              </div>
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="mt-6 pt-4 border-t border-cyber-rose/40 flex items-center justify-between">
          <div className="flex items-center space-x-2 text-cyber-rose text-xs font-bold tracking-wider">
            <span className="w-3 h-3 rounded-full bg-cyber-rose animate-ping"></span>
            <span>SYSTEM WAITING FOR OPERATOR OVERRIDE</span>
          </div>

          <button
            onClick={handleResume}
            className="px-6 py-3 rounded-lg bg-cyber-emerald hover:bg-emerald-400 text-obsidian-950 font-display font-bold text-xs flex items-center space-x-2 shadow-[0_0_25px_rgba(16,185,129,0.5)] hover:scale-105 transition-all uppercase tracking-widest"
          >
            <Play className="w-4 h-4 fill-current" />
            <span>[ RESUME ALL OPERATIONS ]</span>
          </button>
        </div>
      </div>
    </div>
  );
};

