import React, { useState } from 'react';
import { 
  Cpu, 
  ArrowRight, 
  Activity,
  CheckCircle2
} from 'lucide-react';
import { AiDecision, ModelRoute } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

interface AiIntelligenceProps {
  decisions: AiDecision[];
  routes: ModelRoute[];
}

export const AiIntelligence: React.FC<AiIntelligenceProps> = ({
  decisions,
  routes
}) => {
  const [subTab, setSubTab] = useState<'decisions' | 'routing' | 'activity'>('decisions');

  const handleSubTabChange = (tab: any) => {
    soundEngine.playTabSwitch();
    setSubTab(tab);
  };

  return (
    <div className="space-y-6 font-mono text-slate-100 pb-10">
      {/* Page Navigation Tabs */}
      <div className="glass-panel border border-slate-800 p-3 rounded-xl flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => handleSubTabChange('decisions')}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              subTab === 'decisions'
                ? 'bg-cyber-cyan text-obsidian-950 font-display shadow-[0_0_15px_rgba(0,240,255,0.4)]'
                : 'text-slate-400 hover:text-slate-200 bg-obsidian-900/60 border border-slate-800'
            }`}
          >
            <Cpu className="w-4 h-4" />
            <span>AI DECISIONS</span>
          </button>

          <button
            onClick={() => handleSubTabChange('routing')}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              subTab === 'routing'
                ? 'bg-cyber-cyan text-obsidian-950 font-display shadow-[0_0_15px_rgba(0,240,255,0.4)]'
                : 'text-slate-400 hover:text-slate-200 bg-obsidian-900/60 border border-slate-800'
            }`}
          >
            <ArrowRight className="w-4 h-4" />
            <span>MODEL ROUTING</span>
          </button>

          <button
            onClick={() => handleSubTabChange('activity')}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              subTab === 'activity'
                ? 'bg-cyber-cyan text-obsidian-950 font-display shadow-[0_0_15px_rgba(0,240,255,0.4)]'
                : 'text-slate-400 hover:text-slate-200 bg-obsidian-900/60 border border-slate-800'
            }`}
          >
            <Activity className="w-4 h-4" />
            <span>AI ACTIVITY FEED</span>
          </button>
        </div>

        <span className="text-[10px] text-cyber-cyan font-bold px-3 py-1 rounded-lg bg-obsidian-950 border border-cyber-cyan/40 hidden sm:inline-block">
          AUDITABLE METADATA ONLY
        </span>
      </div>

      {/* SUB-TAB 1: AI DECISIONS */}
      {subTab === 'decisions' && (
        <div className="space-y-5">
          <div className="glass-panel border border-slate-800 p-4 rounded-xl flex items-center justify-between">
            <h2 className="text-xs font-display font-bold uppercase tracking-wider text-slate-100 neon-text-cyan">
              AUDITABLE OPERATIONAL AI DECISIONS FEED ({decisions.length})
            </h2>
            <p className="text-[11px] text-slate-400">Hidden chain-of-thought suppressed per security policy.</p>
          </div>

          <div className="space-y-4">
            {decisions.map((dec) => (
              <div key={dec.id} className="glass-panel border border-slate-800 rounded-xl p-5 space-y-3 font-mono text-xs hover:border-cyber-cyan/40 transition-colors">
                <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                  <div className="flex items-center space-x-3">
                    <span className="px-2.5 py-1 rounded bg-cyan-950/80 border border-cyber-cyan/50 text-cyber-cyan text-[10px] font-bold uppercase">
                      {dec.agent}
                    </span>
                    <span className="text-slate-400 text-[11px]">{dec.timestamp}</span>
                  </div>

                  <div className="flex items-center space-x-2">
                    <span className="text-slate-400 text-[11px]">CONFIDENCE:</span>
                    <span className="px-2.5 py-0.5 rounded bg-emerald-950 border border-emerald-800 text-cyber-emerald font-bold">
                      {dec.confidence}%
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                  <div className="space-y-2.5">
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase font-bold mb-0.5">Goal:</span>
                      <span className="text-slate-200 font-semibold">{dec.goal}</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase font-bold mb-0.5">Capability & Tool:</span>
                      <span className="text-cyber-cyan font-bold">{dec.capability} → <code className="text-slate-100">{dec.selectedTool}</code></span>
                    </div>
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase font-bold mb-0.5">Operational Rationale:</span>
                      <p className="text-slate-300 bg-obsidian-950 p-3 rounded-lg border border-slate-900 leading-relaxed">{dec.reason}</p>
                    </div>
                  </div>

                  <div className="space-y-2.5">
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase font-bold mb-0.5">Execution Result:</span>
                      <p className="text-cyber-emerald bg-obsidian-950 p-3 rounded-lg border border-slate-900 font-mono leading-relaxed">{dec.result}</p>
                    </div>
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase font-bold mb-0.5">Next Recommended Action:</span>
                      <span className="text-cyber-amber font-bold block">{dec.nextAction}</span>
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
        <div className="space-y-5">
          <div className="glass-panel border border-slate-800 p-5 rounded-xl">
            <h2 className="text-xs font-display font-bold uppercase tracking-wider text-slate-100 mb-1 neon-text-cyan">
              DYNAMIC MODEL ROUTING PIPELINE
            </h2>
            <p className="text-xs text-slate-400">
              FORGE dispatches requests to the optimal AI provider based on latency, model capabilities, and escalation requirements.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {routes.map((rt, idx) => (
              <div key={idx} className="glass-panel border border-slate-800 rounded-xl p-5 space-y-3 font-mono text-xs hover:border-cyber-cyan/40 transition-colors">
                <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                  <span className="font-bold text-slate-100 text-sm font-display">{rt.task}</span>
                  <span className="px-2.5 py-0.5 rounded bg-cyan-950 border border-cyber-cyan/50 text-cyber-cyan text-[10px] font-bold">
                    LATENCY: {rt.latency}
                  </span>
                </div>

                <div className="space-y-2.5">
                  <div className="flex items-center space-x-2">
                    <span className="text-slate-400 text-[11px] font-bold">Selected Provider:</span>
                    <span className="text-cyber-emerald font-bold">{rt.selectedProvider}</span>
                  </div>

                  <div className="flex items-center space-x-2">
                    <span className="text-slate-400 text-[11px] font-bold">Model:</span>
                    <span className="text-slate-200 font-semibold">{rt.model}</span>
                  </div>

                  <div>
                    <span className="text-slate-500 block text-[10px] uppercase font-bold mb-0.5">Routing Rationale:</span>
                    <p className="text-slate-300 bg-obsidian-950 p-3 rounded-lg border border-slate-900">{rt.reason}</p>
                  </div>

                  <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px]">
                    <span className="text-slate-500">Fallback Path:</span>
                    <span className="text-cyber-amber font-bold">{rt.fallback}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SUB-TAB 3: AI ACTIVITY FEED */}
      {subTab === 'activity' && (
        <div className="space-y-5">
          <div className="glass-panel border border-slate-800 p-4 rounded-xl flex items-center justify-between">
            <h2 className="text-xs font-display font-bold uppercase tracking-wider text-slate-100 neon-text-cyan">
              REAL-TIME AI REQUEST & RESPONSE LOGS
            </h2>
            <span className="text-xs text-cyber-emerald font-bold flex items-center space-x-1">
              <CheckCircle2 className="w-4 h-4" />
              <span>0 FAILURES / 34 REQUESTS</span>
            </span>
          </div>

          <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-5 font-mono text-xs space-y-2.5 max-h-96 overflow-y-auto shadow-inner">
            <div className="p-3 rounded-lg bg-obsidian-900 border border-slate-800 flex items-center justify-between hover:border-cyber-cyan/30 transition-colors">
              <span className="text-slate-300 font-semibold">18:42:01 • RECON → Cerebras (Llama 3.3 70B)</span>
              <span className="text-cyber-emerald font-bold">HTTP 200 • 120ms</span>
            </div>
            <div className="p-3 rounded-lg bg-obsidian-900 border border-slate-800 flex items-center justify-between hover:border-cyber-cyan/30 transition-colors">
              <span className="text-slate-300 font-semibold">18:42:04 • WEB → Gemini 1.5 Pro</span>
              <span className="text-cyber-emerald font-bold">HTTP 200 • 420ms</span>
            </div>
            <div className="p-3 rounded-lg bg-obsidian-900 border border-slate-800 flex items-center justify-between hover:border-cyber-cyan/30 transition-colors">
              <span className="text-slate-300 font-semibold">18:42:15 • WEB → AgentRouter / Claude Code CLI</span>
              <span className="text-cyber-emerald font-bold">HTTP 200 • 890ms</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

