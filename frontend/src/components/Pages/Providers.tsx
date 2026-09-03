import React, { useState } from 'react';
import { 
  Server, 
  RefreshCw, 
  Zap, 
  Sliders, 
  BarChart3, 
  ShieldCheck
} from 'lucide-react';
import { ProviderInfo } from '../../types';

interface ProvidersProps {
  providers: ProviderInfo[];
  onTestConnection: (providerName: string) => void;
}

export const Providers: React.FC<ProvidersProps> = ({
  providers,
  onTestConnection
}) => {
  const [testingName, setTestingName] = useState<string | null>(null);

  const handleTest = (name: string) => {
    setTestingName(name);
    onTestConnection(name);
    setTimeout(() => setTestingName(null), 1500);
  };

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Header Controls */}
      <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-base font-bold text-slate-100 uppercase tracking-wider flex items-center space-x-2">
            <Server className="w-5 h-5 text-cyan-400" />
            <span>AI INFRASTRUCTURE CONTROL CENTER</span>
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Manage provider routing, fallback priorities, API transports, and AgentRouter CLI wrappers.
          </p>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={() => handleTest('ALL')}
            className="px-3 py-1.5 rounded bg-cyan-950 hover:bg-cyan-900 border border-cyan-700 text-cyan-300 text-xs font-bold flex items-center space-x-1.5"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>REFRESH STATUS</span>
          </button>
          <button
            className="px-3 py-1.5 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 text-xs font-bold flex items-center space-x-1.5"
          >
            <BarChart3 className="w-3.5 h-3.5" />
            <span>VIEW USAGE</span>
          </button>
        </div>
      </div>

      {/* Security Banner */}
      <div className="p-3 bg-cyan-950/20 border border-cyan-500/30 rounded-lg text-xs text-cyan-300 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <ShieldCheck className="w-4 h-4 text-cyan-400" />
          <span>SECURITY POLICY: Provider API credentials are securely managed server-side. Keys are never exposed in UI telemetry.</span>
        </div>
        <span className="font-bold text-[10px] uppercase text-emerald-400">ENCRYPTED VAULT ACTIVE</span>
      </div>

      {/* Providers Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {providers.map((p) => (
          <div
            key={p.name}
            className={`bg-[#0b1019] border rounded-lg p-4 space-y-3 flex flex-col justify-between ${
              p.name.includes('AgentRouter')
                ? 'border-emerald-500/40 shadow-[0_0_12px_rgba(16,185,129,0.1)]'
                : 'border-slate-800'
            }`}
          >
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center space-x-2">
                  <span className={`w-2 h-2 rounded-full ${p.status === 'HEALTHY' ? 'bg-emerald-400' : 'bg-amber-400'}`}></span>
                  <h2 className="font-bold text-slate-100 text-sm">{p.name}</h2>
                </div>
                <span className="px-2 py-0.5 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 text-[10px] font-bold">
                  {p.status}
                </span>
              </div>

              {p.routerNote && (
                <p className="text-[11px] text-cyan-300 bg-cyan-950/30 p-2 rounded border border-cyan-900 mb-2 leading-tight">
                  {p.routerNote}
                </p>
              )}

              <div className="space-y-1.5 text-xs text-slate-300">
                <div className="flex justify-between">
                  <span className="text-slate-500">Model:</span>
                  <span className="font-semibold text-slate-200">{p.model}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Transport:</span>
                  <span className="px-1.5 py-0.2 rounded bg-slate-900 border border-slate-800 text-cyan-400 text-[10px] font-bold">
                    {p.transport}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Latency:</span>
                  <span className="text-emerald-400 font-bold">{p.latency}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Total Requests:</span>
                  <span className="text-slate-200 font-bold">{p.requests}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Quota Remaining:</span>
                  <span className="text-cyan-300 font-bold">{p.quota}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Fallback Priority:</span>
                  <span className="text-amber-400 font-bold">Priority #{p.fallbackPriority}</span>
                </div>
              </div>
            </div>

            {/* Provider Actions */}
            <div className="pt-3 border-t border-slate-800 flex items-center justify-between space-x-2">
              <button
                onClick={() => handleTest(p.name)}
                className="flex-1 py-1.5 rounded bg-cyan-950 hover:bg-cyan-900 border border-cyan-700 text-cyan-300 text-[11px] font-bold flex items-center justify-center space-x-1"
              >
                <Zap className={`w-3 h-3 ${testingName === p.name ? 'animate-spin' : ''}`} />
                <span>{testingName === p.name ? 'TESTING...' : 'TEST CONNECTION'}</span>
              </button>

              <button
                className="py-1.5 px-2.5 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 text-[11px] font-bold flex items-center space-x-1"
              >
                <Sliders className="w-3 h-3" />
                <span>CONFIGURE</span>
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
