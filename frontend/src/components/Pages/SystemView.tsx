import React, { useState } from 'react';
import { 
  Settings, 
  Database, 
  Play, 
  RotateCcw, 
  XCircle, 
  CheckCircle2, 
  FileText
} from 'lucide-react';
import { CheckpointItem, AuditLog } from '../../types';

interface SystemViewProps {
  checkpoints: CheckpointItem[];
  auditLogs: AuditLog[];
  onResumeCheckpoint: (runId: string) => void;
}

export const SystemView: React.FC<SystemViewProps> = ({
  checkpoints,
  auditLogs,
  onResumeCheckpoint
}) => {
  const [activeTab, setActiveTab] = useState<'checkpoints' | 'audit' | 'settings'>('checkpoints');

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Tab Controls */}
      <div className="bg-[#0b1019] border border-slate-800 p-2 rounded-lg flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => setActiveTab('checkpoints')}
            className={`px-4 py-2 rounded text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              activeTab === 'checkpoints'
                ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Database className="w-3.5 h-3.5" />
            <span>CHECKPOINTS ({checkpoints.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('audit')}
            className={`px-4 py-2 rounded text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              activeTab === 'audit'
                ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            <span>AUDIT LOGS</span>
          </button>

          <button
            onClick={() => setActiveTab('settings')}
            className={`px-4 py-2 rounded text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              activeTab === 'settings'
                ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Settings className="w-3.5 h-3.5" />
            <span>SYSTEM SETTINGS</span>
          </button>
        </div>

        <span className="text-[10px] text-emerald-400 font-bold px-3 py-1 rounded bg-emerald-950 border border-emerald-800 hidden sm:inline-block">
          FRAMEWORK VERSION 2.0
        </span>
      </div>

      {/* TAB 1: CHECKPOINTS */}
      {activeTab === 'checkpoints' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100 mb-1">STATE CHECKPOINTS & RUN PERSISTENCE</h2>
            <p className="text-xs text-slate-400">
              Review saved operational state checkpoints, step completion checklists, and restart policies.
            </p>
          </div>

          <div className="space-y-4">
            {checkpoints.map((cp) => (
              <div key={cp.runId} className="bg-[#0b1019] border border-slate-800 rounded-lg p-5 space-y-4 font-mono text-xs">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800 pb-3">
                  <div>
                    <div className="flex items-center space-x-2">
                      <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 text-xs font-bold">
                        {cp.runId}
                      </span>
                      <h3 className="font-bold text-slate-100 text-sm">{cp.challenge}</h3>
                      <span className="text-slate-400 text-xs">• {cp.checkpointName}</span>
                    </div>
                    <p className="text-[11px] text-slate-400 mt-1">Reason: <span className="text-slate-200">{cp.reason}</span></p>
                  </div>

                  <span className={`text-xs px-2.5 py-1 rounded font-bold uppercase ${
                    cp.status === 'ACTIVE' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' :
                    'bg-amber-950 text-amber-400 border border-amber-800'
                  }`}>
                    {cp.status}
                  </span>
                </div>

                {/* Checklist Step Breakdown */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-2">
                    <span className="text-emerald-400 font-bold text-[11px] uppercase block">✓ Completed Steps</span>
                    <ul className="space-y-1 text-[11px] text-slate-300">
                      {cp.completedSteps.map((step, idx) => (
                        <li key={idx} className="flex items-center space-x-2">
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                          <span>{step}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-2">
                    <span className="text-amber-400 font-bold text-[11px] uppercase block">○ Pending Steps</span>
                    <ul className="space-y-1 text-[11px] text-slate-400">
                      {cp.pendingSteps.map((step, idx) => (
                        <li key={idx} className="flex items-center space-x-2">
                          <span className="w-3.5 h-3.5 rounded-full border border-slate-600 inline-block shrink-0"></span>
                          <span>{step}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>

                {/* Checkpoint Action Buttons */}
                <div className="pt-2 border-t border-slate-800/80 flex items-center justify-end space-x-3">
                  <button className="px-3 py-1.5 rounded bg-red-950 hover:bg-red-900 border border-red-800 text-red-300 text-xs font-bold flex items-center space-x-1">
                    <XCircle className="w-3.5 h-3.5" />
                    <span>CANCEL</span>
                  </button>
                  <button className="px-3 py-1.5 rounded bg-amber-950 hover:bg-amber-900 border border-amber-800 text-amber-300 text-xs font-bold flex items-center space-x-1">
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>RESTART FROM CHECKPOINT</span>
                  </button>
                  <button
                    onClick={() => onResumeCheckpoint(cp.runId)}
                    className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold flex items-center space-x-1 uppercase shadow-[0_0_10px_rgba(16,185,129,0.3)]"
                  >
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>RESUME</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 2: AUDIT LOGS */}
      {activeTab === 'audit' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100">FRAMEWORK AUDIT TRAIL LOGS</h2>
            <span className="text-xs text-cyan-400 font-bold">{auditLogs.length} RECORDED ENTRIES</span>
          </div>

          <div className="bg-[#0b1019] border border-slate-800 rounded-lg overflow-x-auto">
            <table className="w-full text-left font-mono text-xs">
              <thead className="bg-[#070b12] border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <tr>
                  <th className="p-3">Timestamp</th>
                  <th className="p-3">Actor</th>
                  <th className="p-3">Executed Action / Command</th>
                  <th className="p-3">Target</th>
                  <th className="p-3">Permission</th>
                  <th className="p-3">Result</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {auditLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-900/40">
                    <td className="p-3 text-slate-500">{log.timestamp}</td>
                    <td className="p-3 font-bold text-slate-200">{log.actor}</td>
                    <td className="p-3 font-mono text-cyan-300">{log.action}</td>
                    <td className="p-3 text-slate-200">{log.target}</td>
                    <td className="p-3">
                      <span className="px-1.5 py-0.2 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 text-[10px] font-bold">
                        {log.permission}
                      </span>
                    </td>
                    <td className="p-3">
                      <span className="px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] font-bold">
                        {log.result}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* TAB 3: SYSTEM SETTINGS */}
      {activeTab === 'settings' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100 mb-1">SYSTEM CONFIGURATION & SAFETY POLICIES</h2>
            <p className="text-xs text-slate-400">Configure global execution limits, AI budget caps, and workspace paths.</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
            <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg space-y-3">
              <h3 className="font-bold text-slate-100 uppercase border-b border-slate-800 pb-2">EXECUTION & PRIVILEGE POLICY</h3>
              <div className="space-y-2 text-slate-300">
                <div className="flex justify-between">
                  <span className="text-slate-400">Execution Mode:</span>
                  <span className="text-cyan-300 font-bold">CTF_OFFENSIVE_CONTROLLED</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Sudo Privileges:</span>
                  <span className="text-emerald-400 font-bold">DISABLED</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Command Timeout:</span>
                  <span className="text-slate-100 font-bold">300 seconds</span>
                </div>
              </div>
            </div>

            <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg space-y-3">
              <h3 className="font-bold text-slate-100 uppercase border-b border-slate-800 pb-2">AI ROUTING & BUDGET PROTECTION</h3>
              <div className="space-y-2 text-slate-300">
                <div className="flex justify-between">
                  <span className="text-slate-400">Daily LLM Budget Cap:</span>
                  <span className="text-emerald-400 font-bold">$5.00 USD</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Current Spend:</span>
                  <span className="text-cyan-300 font-bold">$0.42 USD</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Fallback Routing:</span>
                  <span className="text-emerald-400 font-bold">AUTOMATIC</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
