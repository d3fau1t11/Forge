import React, { useState } from 'react';
import { 
  Cpu, 
  ArrowRight, 
  Activity
} from 'lucide-react';
import { AiDecision, ModelRoute } from '../../types';

interface AiIntelligenceProps {
  decisions: AiDecision[];
  routes: ModelRoute[];
}

export const AiIntelligence: React.FC<AiIntelligenceProps> = ({
  decisions,
  routes
}) => {
  const [subTab, setSubTab] = useState<'decisions' | 'routing' | 'activity'>('decisions');

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Page Navigation Tabs */}
      <div className="bg-[#0b1019] border border-slate-800 p-2 rounded-lg flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => setSubTab('decisions')}
            className={`px-4 py-2 rounded text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              subTab === 'decisions'
                ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>AI DECISIONS</span>
          </button>

          <button
            onClick={() => setSubTab('routing')}
            className={`px-4 py-2 rounded text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              subTab === 'routing'
                ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <ArrowRight className="w-3.5 h-3.5" />
            <span>MODEL ROUTING</span>
          </button>

          <button
            onClick={() => setSubTab('activity')}
            className={`px-4 py-2 rounded text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              subTab === 'activity'
                ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            <span>AI ACTIVITY FEED</span>
          </button>
        </div>

        <span className="text-[10px] text-cyan-400 font-bold px-3 py-1 rounded bg-cyan-950 border border-cyan-800 hidden sm:inline-block">
          AUDITABLE METADATA ONLY
        </span>
      </div>

      {/* SUB-TAB 1: AI DECISIONS */}
      {subTab === 'decisions' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100">
              AUDITABLE OPERATIONAL AI DECISIONS FEED ({decisions.length})
            </h2>
            <p className="text-[11px] text-slate-400">Hidden chain-of-thought suppressed per security policy.</p>
          </div>

          <div className="space-y-3">
            {decisions.map((dec) => (
              <div key={dec.id} className="bg-[#0b1019] border border-slate-800 rounded-lg p-5 space-y-3 font-mono text-xs">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
                  <div className="flex items-center space-x-3">
                    <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 text-[10px] font-bold uppercase">
                      {dec.agent}
                    </span>
                    <span className="text-slate-400 text-[11px]">{dec.timestamp}</span>
                  </div>

                  <div className="flex items-center space-x-2">
                    <span className="text-slate-400 text-[11px]">CONFIDENCE:</span>
                    <span className="px-2 py-0.5 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 font-bold">
                      {dec.confidence}%
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                  <div className="space-y-2">
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase">Goal:</span>
                      <span className="text-slate-200 font-semibold">{dec.goal}</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase">Capability & Tool:</span>
                      <span className="text-cyan-300 font-bold">{dec.capability} → <code className="text-slate-100">{dec.selectedTool}</code></span>
                    </div>
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase">Operational Rationale:</span>
                      <p className="text-slate-300 bg-[#070b12] p-2 rounded border border-slate-900 leading-relaxed">{dec.reason}</p>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase">Execution Result:</span>
                      <p className="text-emerald-300 bg-[#070b12] p-2 rounded border border-slate-900 font-mono leading-relaxed">{dec.result}</p>
                    </div>
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase">Next Recommended Action:</span>
                      <span className="text-amber-300 font-bold block">{dec.nextAction}</span>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SUB-TAB 2: MODEL ROUTING */}
      {subTab === 'routing' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100 mb-1">DYNAMIC MODEL ROUTING PIPELINE</h2>
            <p className="text-xs text-slate-400">
              FORGE dispatches requests to the optimal AI provider based on latency, model capabilities, and escalation requirements.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {routes.map((rt, idx) => (
              <div key={idx} className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3 font-mono text-xs">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                  <span className="font-bold text-slate-100 text-sm">{rt.task}</span>
                  <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 text-[10px] font-bold">
                    LATENCY: {rt.latency}
                  </span>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center space-x-2">
                    <span className="text-slate-400 text-[11px]">Selected Provider:</span>
                    <span className="text-emerald-400 font-bold">{rt.selectedProvider}</span>
                  </div>

                  <div className="flex items-center space-x-2">
                    <span className="text-slate-400 text-[11px]">Model:</span>
                    <span className="text-slate-200 font-semibold">{rt.model}</span>
                  </div>

                  <div>
                    <span className="text-slate-500 block text-[10px] uppercase">Routing Rationale:</span>
                    <p className="text-slate-300 bg-[#070b12] p-2 rounded border border-slate-900 mt-1">{rt.reason}</p>
                  </div>

                  <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px]">
                    <span className="text-slate-500">Fallback Path:</span>
                    <span className="text-amber-400 font-semibold">{rt.fallback}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SUB-TAB 3: AI ACTIVITY FEED */}
      {subTab === 'activity' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100">
              REAL-TIME AI REQUEST & RESPONSE LOGS
            </h2>
            <span className="text-xs text-emerald-400 font-bold">0 FAILURES / 34 REQUESTS</span>
          </div>

          <div className="bg-[#05080e] border border-slate-900 rounded p-4 font-mono text-xs space-y-2 max-h-96 overflow-y-auto">
            <div className="p-2 rounded bg-slate-900/60 border border-slate-800 flex items-center justify-between">
              <span className="text-slate-400">18:42:01 • RECON → Cerebras (Llama 3.3 70B)</span>
              <span className="text-emerald-400 font-bold">HTTP 200 • 120ms</span>
            </div>
            <div className="p-2 rounded bg-slate-900/60 border border-slate-800 flex items-center justify-between">
              <span className="text-slate-400">18:42:04 • WEB → Gemini 1.5 Pro</span>
              <span className="text-emerald-400 font-bold">HTTP 200 • 420ms</span>
            </div>
            <div className="p-2 rounded bg-slate-900/60 border border-slate-800 flex items-center justify-between">
              <span className="text-slate-400">18:42:15 • WEB → AgentRouter / Claude Code CLI</span>
              <span className="text-emerald-400 font-bold">HTTP 200 • 890ms</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
