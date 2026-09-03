import React from 'react';
import { AlertOctagon, ShieldAlert, Play, Database, FileText } from 'lucide-react';

interface EmergencyStopModalProps {
  isOpen: boolean;
  onResume: () => void;
}

export const EmergencyStopModal: React.FC<EmergencyStopModalProps> = ({
  isOpen,
  onResume
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-md p-4 select-none">
      <div className="w-full max-w-xl bg-[#0d0709] border border-red-600/80 rounded-lg p-6 shadow-[0_0_50px_rgba(220,38,38,0.35)] font-mono text-slate-100 relative">
        {/* Glowing Red Warning Header */}
        <div className="flex items-center space-x-3 border-b border-red-900/60 pb-4 mb-5">
          <div className="w-12 h-12 rounded bg-red-950/80 border border-red-500 flex items-center justify-center text-red-500 shadow-[0_0_15px_rgba(239,68,68,0.4)] animate-pulse">
            <AlertOctagon className="w-7 h-7" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-widest text-red-400">EMERGENCY STOP ACTIVATED</h1>
            <p className="text-xs text-red-300/80 font-semibold uppercase">● OPERATIONS HALTED BY OPERATOR KILL SWITCH</p>
          </div>
        </div>

        {/* Stopped Components Section */}
        <div className="space-y-4 text-xs">
          <div className="bg-red-950/30 border border-red-900/50 rounded p-4 space-y-2">
            <div className="flex items-center space-x-2 text-red-400 font-bold uppercase text-[11px]">
              <ShieldAlert className="w-4 h-4" />
              <span>FORGE Has Suspended All Active Exploitation Loops:</span>
            </div>
            <ul className="grid grid-cols-2 gap-2 text-slate-300 font-mono text-[11px] pt-1">
              <li className="flex items-center space-x-2">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                <span>AI Agent Reasoning</span>
              </li>
              <li className="flex items-center space-x-2">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                <span>Tool & Binary Execution</span>
              </li>
              <li className="flex items-center space-x-2">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                <span>New AI LLM Requests</span>
              </li>
              <li className="flex items-center space-x-2">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                <span>Browser Automation</span>
              </li>
              <li className="col-span-2 flex items-center space-x-2 text-red-300 font-semibold">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                <span>Autonomous Pipeline Operations</span>
              </li>
            </ul>
          </div>

          {/* Preserved Data Notice */}
          <div className="bg-slate-900/60 border border-slate-800 rounded p-4 space-y-2">
            <div className="flex items-center space-x-2 text-emerald-400 font-bold uppercase text-[11px]">
              <Database className="w-4 h-4" />
              <span>Preserved Operational State:</span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-slate-300 text-[11px]">
              <div className="flex items-center space-x-2">
                <FileText className="w-3.5 h-3.5 text-cyan-400" />
                <span>Evidence & Flags Intact</span>
              </div>
              <div className="flex items-center space-x-2">
                <FileText className="w-3.5 h-3.5 text-cyan-400" />
                <span>Audit & Terminal Logs Saved</span>
              </div>
              <div className="flex items-center space-x-2">
                <FileText className="w-3.5 h-3.5 text-cyan-400" />
                <span>State Checkpoints Retained</span>
              </div>
              <div className="flex items-center space-x-2">
                <FileText className="w-3.5 h-3.5 text-cyan-400" />
                <span>Target Profiles Preserved</span>
              </div>
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="mt-6 pt-4 border-t border-red-900/60 flex items-center justify-between">
          <div className="flex items-center space-x-2 text-red-400 text-xs font-semibold">
            <span className="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse"></span>
            <span>● OPERATIONS STOPPED</span>
          </div>

          <button
            onClick={onResume}
            className="px-5 py-2.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center space-x-2 shadow-[0_0_15px_rgba(16,185,129,0.3)] transition-all uppercase"
          >
            <Play className="w-4 h-4 fill-current" />
            <span>[ RESUME OPERATIONS ]</span>
          </button>
        </div>
      </div>
    </div>
  );
};
