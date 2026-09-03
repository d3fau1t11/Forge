import React, { useState } from 'react';
import { 
  Target as TargetIcon, 
  RefreshCw, 
  CheckCircle2, 
  Server, 
  ArrowDown, 
  Edit, 
  Globe
} from 'lucide-react';
import { Target } from '../../types';

interface TargetsProps {
  targets: Target[];
  onRediscover: (id: string) => void;
  onVerify: (id: string) => void;
}

export const Targets: React.FC<TargetsProps> = ({
  targets,
  onRediscover,
  onVerify
}) => {
  const [selectedTargetId, setSelectedTargetId] = useState<string>(targets[0]?.id || 'TARGET-0042');
  const activeTarget = targets.find((t) => t.id === selectedTargetId) || targets[0];

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Concept Architecture Banner */}
      <div className="bg-cyan-950/30 border border-cyan-500/40 rounded-lg p-4 flex items-center justify-between text-xs">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded bg-cyan-900/60 border border-cyan-400 flex items-center justify-center text-cyan-300">
            <TargetIcon className="w-5 h-5" />
          </div>
          <div>
            <h2 className="font-bold text-cyan-300 uppercase tracking-wider">TARGET IDENTITY ARCHITECTURE</h2>
            <p className="text-slate-400 text-[11px]">
              FORGE investigates persistent TARGET identities rather than relying on dynamic or ephemeral IP allocations.
            </p>
          </div>
        </div>
        <span className="hidden sm:inline-block px-2.5 py-1 rounded bg-cyan-900/40 border border-cyan-700 text-cyan-300 text-[10px] font-bold">
          IDENTITY RESOLUTION ENGINE ACTIVE
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Targets Master List (1 col) */}
        <div className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">KNOWN TARGET IDENTITIES</h2>
            <span className="text-[10px] text-slate-400">{targets.length} REGISTERED</span>
          </div>

          <div className="space-y-2">
            {targets.map((t) => (
              <div
                key={t.id}
                onClick={() => setSelectedTargetId(t.id)}
                className={`p-3 rounded border text-xs cursor-pointer transition-all ${
                  t.id === selectedTargetId
                    ? 'bg-cyan-950/40 border-cyan-500/60 shadow-[0_0_12px_rgba(6,182,212,0.15)] text-slate-100'
                    : 'bg-[#070b12] border-slate-800 text-slate-400 hover:text-slate-200'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-bold text-cyan-300">{t.id}</span>
                  <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 font-bold">
                    ● {t.status}
                  </span>
                </div>
                <div className="text-[11px] text-slate-300">
                  <span>IP: <span className="font-bold text-slate-100">{t.currentIp}</span></span>
                  <span className="text-slate-500 ml-2">({t.hostname})</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Target Details Inspector (2 cols) */}
        <div className="lg:col-span-2 bg-[#0b1019] border border-slate-800 rounded-lg p-5 space-y-5">
          {/* Target Title & Controls Header */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-4">
            <div>
              <div className="flex items-center space-x-2">
                <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 text-xs font-bold">
                  {activeTarget.id}
                </span>
                <h1 className="text-lg font-bold text-slate-100 tracking-wider">{activeTarget.hostname}</h1>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Current Address: <span className="text-cyan-300 font-bold">{activeTarget.currentIp}</span> • Discovery: {activeTarget.discoveryMethod}
              </p>
            </div>

            {/* Actions */}
            <div className="flex items-center space-x-2">
              <button
                onClick={() => onVerify(activeTarget.id)}
                className="px-3 py-1.5 rounded bg-emerald-950 hover:bg-emerald-900 border border-emerald-700 text-emerald-300 font-bold text-xs flex items-center space-x-1.5"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>VERIFY</span>
              </button>
              <button
                onClick={() => onRediscover(activeTarget.id)}
                className="px-3 py-1.5 rounded bg-cyan-950 hover:bg-cyan-900 border border-cyan-700 text-cyan-300 font-bold text-xs flex items-center space-x-1.5"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>REDISCOVER</span>
              </button>
              <button
                className="px-3 py-1.5 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 font-bold text-xs flex items-center space-x-1.5"
              >
                <Edit className="w-3.5 h-3.5" />
                <span>UPDATE PROFILE</span>
              </button>
            </div>
          </div>

          {/* Lineage & Service Breakdown */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* IP Address Lineage History */}
            <div className="bg-[#070b12] border border-slate-800 rounded p-4 space-y-3">
              <h3 className="text-xs font-bold text-slate-200 uppercase flex items-center space-x-2">
                <Globe className="w-4 h-4 text-cyan-400" />
                <span>ADDRESS HISTORY LINEAGE</span>
              </h3>

              <div className="p-3 bg-[#05080e] border border-slate-900 rounded space-y-2 font-mono text-xs">
                {activeTarget.addressHistory.map((addr, idx) => (
                  <React.Fragment key={addr}>
                    <div className="flex items-center justify-between px-3 py-1.5 rounded bg-slate-900/80 border border-slate-800">
                      <span className="text-slate-300 font-bold">{addr}</span>
                      <span className="text-[10px] text-slate-500">
                        {idx === activeTarget.addressHistory.length - 1 ? 'CURRENT ACTIVE IP' : 'PREVIOUS INSTANCE'}
                      </span>
                    </div>
                    {idx < activeTarget.addressHistory.length - 1 && (
                      <div className="flex justify-center my-0.5 text-cyan-400">
                        <ArrowDown className="w-4 h-4 animate-bounce" />
                      </div>
                    )}
                  </React.Fragment>
                ))}
              </div>
            </div>

            {/* Verified Services List */}
            <div className="bg-[#070b12] border border-slate-800 rounded p-4 space-y-3">
              <h3 className="text-xs font-bold text-slate-200 uppercase flex items-center space-x-2">
                <Server className="w-4 h-4 text-cyan-400" />
                <span>VERIFIED SERVICES & PORTS</span>
              </h3>

              <div className="space-y-2">
                {activeTarget.services.map((svc) => (
                  <div key={svc.port} className="p-2.5 rounded bg-[#05080e] border border-slate-800 text-xs flex items-center justify-between">
                    <div>
                      <div className="flex items-center space-x-2">
                        <span className="font-bold text-cyan-400">{svc.port}/{svc.proto}</span>
                        <span className="font-semibold text-slate-200">{svc.service}</span>
                      </div>
                      <span className="text-[10px] text-slate-400 block">{svc.version || 'Version unverified'}</span>
                    </div>
                    <span className="px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] font-bold">
                      OPEN
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Technology Profile */}
          <div className="bg-[#070b12] border border-slate-800 rounded p-4 space-y-2">
            <h3 className="text-xs font-bold text-slate-200 uppercase">IDENTIFIED TECHNOLOGY STACK</h3>
            <div className="flex flex-wrap gap-2 pt-1">
              {activeTarget.technologies.map((t) => (
                <span key={t} className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-900 text-cyan-300 text-xs font-semibold">
                  {t}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
