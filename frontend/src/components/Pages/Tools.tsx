import React from 'react';
import { 
  Wrench, 
  Zap 
} from 'lucide-react';
import { ToolItem } from '../../types';

interface ToolsProps {
  tools: ToolItem[];
}

export const Tools: React.FC<ToolsProps> = ({ tools }) => {
  const categories = Array.from(new Set(tools.map((t) => t.capabilityCategory)));

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Architectural Concept Banner */}
      <div className="bg-[#0b1019] border border-cyan-500/40 rounded-lg p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-[0_0_20px_rgba(6,182,212,0.1)]">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded bg-cyan-950 border border-cyan-500 flex items-center justify-center text-cyan-400">
            <Wrench className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-base font-bold text-cyan-300 uppercase tracking-wider">FORGE CAPABILITY TOOL MANAGER</h1>
            <p className="text-xs text-slate-300 font-semibold mt-0.5">
              "AI decides WHAT capability is needed. Tool Manager decides HOW to execute it."
            </p>
          </div>
        </div>
        <div className="flex items-center space-x-2 text-xs">
          <span className="px-3 py-1 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 font-bold">
            {tools.length} TOOLS REGISTERED
          </span>
        </div>
      </div>

      {/* Tools Grouped by Capability */}
      <div className="space-y-6">
        {categories.map((cat) => {
          const categoryTools = tools.filter((t) => t.capabilityCategory === cat);
          if (categoryTools.length === 0) return null;

          return (
            <div key={cat} className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
                <div className="flex items-center space-x-2">
                  <Zap className="w-4 h-4 text-cyan-400" />
                  <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">{cat} CAPABILITY</h2>
                </div>
                <span className="text-[10px] text-slate-400">{categoryTools.length} REGISTERED BINARIES</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {categoryTools.map((t) => (
                  <div key={t.name} className="p-3 bg-[#070b12] border border-slate-800 rounded text-xs space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="font-bold text-cyan-300 text-sm">{t.name}</span>
                        {t.installed ? (
                          <span className="px-1.5 py-0.2 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 text-[9px] font-bold">
                            INSTALLED
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.2 rounded bg-slate-900 border border-slate-800 text-slate-500 text-[9px]">
                            NOT INSTALLED
                          </span>
                        )}
                      </div>
                      <span className={`text-[10px] px-1.5 py-0.2 rounded font-bold uppercase ${
                        t.status === 'EXECUTING' ? 'bg-amber-950 text-amber-400 border border-amber-800 animate-pulse' :
                        'bg-slate-900 text-slate-400 border border-slate-800'
                      }`}>
                        {t.status}
                      </span>
                    </div>

                    <div className="space-y-1 text-[11px] text-slate-400">
                      <div><span className="text-slate-500">Binary Path:</span> <code className="text-slate-300">{t.binary}</code></div>
                      <div><span className="text-slate-500">Version:</span> <span className="text-slate-300">{t.version}</span></div>
                      <div><span className="text-slate-500">Executions:</span> <span className="text-cyan-300 font-bold">{t.executionCount} runs</span></div>
                      <div><span className="text-slate-500">Last Executed:</span> <span className="text-slate-300">{t.lastExecution}</span></div>
                      <div><span className="text-slate-500">Fallback Designation:</span> <span className="text-amber-400 font-semibold">{t.fallbackTool}</span></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
