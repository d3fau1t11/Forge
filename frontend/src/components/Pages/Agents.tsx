import React, { useState } from 'react';
import { 
  Users, 
  Cpu
} from 'lucide-react';
import { AgentInfo } from '../../types';

interface AgentsProps {
  agents: AgentInfo[];
}

export const Agents: React.FC<AgentsProps> = ({ agents }) => {
  const [selectedAgentId, setSelectedAgentId] = useState<string>(agents[0]?.id || 'ag-orchestrator');
  const activeAgent = agents.find((a) => a.id === selectedAgentId) || agents[0];

  const runningCount = agents.filter((a) => a.status === 'RUNNING' || a.status === 'ANALYZING').length;
  const standbyCount = agents.length - runningCount;

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Header Banner */}
      <div className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-base font-bold text-slate-100 uppercase tracking-wider flex items-center space-x-2">
            <Users className="w-5 h-5 text-cyan-400" />
            <span>AUTONOMOUS AI AGENT TELEMETRY</span>
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Monitor real-time agent objective execution, model assignments, tool selection, and operational checkpoints.
          </p>
        </div>
        <div className="flex items-center space-x-3 text-xs">
          <span className="px-2.5 py-1 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 font-bold">
            {runningCount} RUNNING
          </span>
          <span className="px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-slate-400">
            {standbyCount} STANDBY
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Agent Telemetry Cards (2 cols) */}
        <div className="lg:col-span-2 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {agents.map((ag) => (
              <div
                key={ag.id}
                onClick={() => setSelectedAgentId(ag.id)}
                className={`p-4 rounded-lg border cursor-pointer transition-all ${
                  ag.id === selectedAgentId
                    ? 'bg-cyan-950/30 border-cyan-500/60 shadow-[0_0_15px_rgba(6,182,212,0.15)]'
                    : 'bg-[#0b1019] border-slate-800 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center space-x-2">
                    <span className={`w-2.5 h-2.5 rounded-full ${
                      ag.status === 'RUNNING' ? 'bg-emerald-400 animate-pulse' :
                      ag.status === 'ANALYZING' ? 'bg-cyan-400 animate-pulse' :
                      'bg-slate-600'
                    }`}></span>
                    <span className="font-bold text-slate-100 tracking-wide text-sm">{ag.name}</span>
                  </div>
                  <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase ${
                    ag.status === 'RUNNING' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' :
                    ag.status === 'ANALYZING' ? 'bg-cyan-950 text-cyan-400 border border-cyan-800' :
                    'bg-slate-900 text-slate-500 border border-slate-800'
                  }`}>
                    {ag.status}
                  </span>
                </div>

                <div className="space-y-1.5 text-xs">
                  <p className="text-slate-300 line-clamp-1">
                    <span className="text-slate-500">OBJECTIVE:</span> {ag.currentObjective}
                  </p>

                  <div className="grid grid-cols-2 gap-2 text-[11px] pt-2 border-t border-slate-800/80">
                    <div>
                      <span className="text-slate-500 block">MODEL:</span>
                      <span className="text-cyan-300 font-semibold truncate block">{ag.selectedModel}</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">LAST TOOL:</span>
                      <span className="text-slate-200 font-mono block">{ag.lastTool}</span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-[10px] text-slate-400 pt-1">
                    <span>ACTIONS: <span className="text-slate-200 font-bold">{ag.actionsCompleted}</span></span>
                    <span>RUNTIME: <span className="text-slate-200 font-bold">{ag.runtime}</span></span>
                    <span>FAILURES: <span className={ag.failures > 0 ? 'text-red-400 font-bold' : 'text-slate-400'}>{ag.failures}</span></span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Selected Agent Inspector Drawer (1 col) */}
        <div className="bg-[#0b1019] border border-slate-800 rounded-lg p-5 space-y-4 font-mono text-xs">
          <div className="border-b border-slate-800 pb-3 flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <Cpu className="w-4 h-4 text-cyan-400" />
              <h2 className="font-bold text-slate-100 uppercase tracking-wider">{activeAgent.name} OPERATIONAL METADATA</h2>
            </div>
            <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-bold">
              AUDITABLE
            </span>
          </div>

          <div className="space-y-3">
            <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-1">
              <span className="text-slate-500 text-[10px] uppercase block">Current Capability Requested</span>
              <span className="text-cyan-300 font-bold text-xs">{activeAgent.currentCapability}</span>
            </div>

            <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-1">
              <span className="text-slate-500 text-[10px] uppercase block">Selected LLM Provider</span>
              <span className="text-emerald-400 font-bold text-xs">{activeAgent.selectedModel}</span>
            </div>

            <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-1">
              <span className="text-slate-500 text-[10px] uppercase block">Last Execution Result</span>
              <span className="text-slate-200 text-xs block leading-relaxed">{activeAgent.lastResult}</span>
            </div>

            <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-1">
              <span className="text-slate-500 text-[10px] uppercase block">Checkpoint Telemetry</span>
              <span className="text-amber-400 font-bold text-xs">{activeAgent.checkpointStatus}</span>
            </div>
          </div>

          <div className="p-3 bg-cyan-950/20 border border-cyan-500/30 rounded text-[11px] text-cyan-300/90 leading-relaxed">
            <span className="font-bold block mb-1 text-cyan-400">OPERATIONAL POLICY NOTE:</span>
            Raw model chain-of-thought is suppressed per security policy. Only verified, auditable action metadata is surfaced to operator console.
          </div>
        </div>
      </div>
    </div>
  );
};
