import React, { useState, useEffect } from 'react';
import { Download, Package, X, AlertTriangle, Terminal, CheckCircle, Loader2 } from 'lucide-react';

export interface PackageInstallRequest {
  requestId: string;
  challengeId: string;
  challengeName: string;
  packageName: string;
  importName: string;
  errorSnippet: string;
  timestamp: string;
}

interface PackageInstallModalProps {
  requests: PackageInstallRequest[];
  onApprove: (requestId: string, packageName: string) => void;
  onDismiss: (requestId: string) => void;
}

export const PackageInstallModal: React.FC<PackageInstallModalProps> = ({
  requests,
  onApprove,
  onDismiss
}) => {
  const [installingIds, setInstallingIds] = useState<Set<string>>(new Set());
  const [completedIds, setCompletedIds] = useState<Set<string>>(new Set());

  // Auto-dismiss completed installs after 2s
  useEffect(() => {
    completedIds.forEach((id) => {
      const timer = setTimeout(() => {
        onDismiss(id);
        setCompletedIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }, 2000);
      return () => clearTimeout(timer);
    });
  }, [completedIds, onDismiss]);

  if (requests.length === 0) return null;

  const handleApprove = (req: PackageInstallRequest) => {
    setInstallingIds((prev) => new Set(prev).add(req.requestId));
    onApprove(req.requestId, req.packageName);
    // Simulate completion feedback (backend will send WS event)
    setTimeout(() => {
      setInstallingIds((prev) => {
        const next = new Set(prev);
        next.delete(req.requestId);
        return next;
      });
      setCompletedIds((prev) => new Set(prev).add(req.requestId));
    }, 8000);
  };

  return (
    <>
      {requests.map((req, index) => (
        <div
          key={req.requestId}
          className="fixed z-50 animate-fadeIn"
          style={{
            top: `${80 + index * 10}px`,
            right: '24px',
            maxWidth: '480px',
            width: '100%'
          }}
        >
          <div className="bg-obsidian-950 border border-amber-500/60 rounded-xl shadow-[0_0_40px_rgba(245,158,11,0.25)] font-mono text-slate-100 overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between bg-amber-500/10 border-b border-amber-500/30 px-4 py-3">
              <div className="flex items-center space-x-3">
                <div className="w-9 h-9 rounded-lg bg-amber-500/20 border border-amber-500/50 flex items-center justify-center text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.3)] animate-pulse">
                  <Package className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-amber-400 tracking-wider uppercase">
                    Package Install Required
                  </h3>
                  <p className="text-[10px] text-slate-400 flex items-center space-x-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping"></span>
                    <span>AI Solver needs a missing dependency</span>
                  </p>
                </div>
              </div>

              <button
                onClick={() => onDismiss(req.requestId)}
                className="p-1.5 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
                title="Skip this install"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Body */}
            <div className="px-4 py-3 space-y-3">
              {/* Package info */}
              <div className="flex items-center space-x-3 bg-obsidian-900/80 border border-slate-800 rounded-lg p-3">
                <Download className="w-5 h-5 text-cyber-cyan flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-slate-400">Missing Package</div>
                  <div className="text-sm font-bold text-cyber-cyan truncate">
                    pip install {req.packageName}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] text-slate-500">Import</div>
                  <code className="text-[11px] text-amber-300">{req.importName}</code>
                </div>
              </div>

              {/* Challenge context */}
              <div className="flex items-center space-x-2 text-[11px] text-slate-400">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                <span>
                  Challenge: <strong className="text-slate-200">{req.challengeName}</strong>
                </span>
              </div>

              {/* Error snippet */}
              <div className="bg-obsidian-900 border border-red-900/40 rounded-lg p-2.5 max-h-20 overflow-y-auto">
                <div className="flex items-center space-x-1.5 mb-1">
                  <Terminal className="w-3 h-3 text-red-400" />
                  <span className="text-[10px] text-red-400 font-bold uppercase tracking-wider">Error Output</span>
                </div>
                <pre className="text-[10px] text-red-300/80 whitespace-pre-wrap leading-relaxed font-mono">
                  {req.errorSnippet}
                </pre>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="px-4 py-3 border-t border-slate-800 flex items-center justify-between gap-3">
              <button
                onClick={() => onDismiss(req.requestId)}
                className="px-4 py-2 rounded-lg bg-obsidian-900 border border-slate-700 hover:border-slate-500 text-slate-400 hover:text-slate-200 text-xs font-bold uppercase tracking-wider transition-all flex items-center space-x-2"
              >
                <X className="w-3.5 h-3.5" />
                <span>Skip</span>
              </button>

              {completedIds.has(req.requestId) ? (
                <div className="px-5 py-2.5 rounded-lg bg-cyber-emerald/20 border border-cyber-emerald text-cyber-emerald text-xs font-bold uppercase tracking-wider flex items-center space-x-2">
                  <CheckCircle className="w-4 h-4" />
                  <span>Installed</span>
                </div>
              ) : installingIds.has(req.requestId) ? (
                <div className="px-5 py-2.5 rounded-lg bg-amber-500/10 border border-amber-500/50 text-amber-400 text-xs font-bold uppercase tracking-wider flex items-center space-x-2 animate-pulse">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Installing...</span>
                </div>
              ) : (
                <button
                  onClick={() => handleApprove(req)}
                  className="px-5 py-2.5 rounded-lg bg-cyber-emerald hover:bg-emerald-400 text-obsidian-950 font-bold text-xs uppercase tracking-widest flex items-center space-x-2 shadow-[0_0_20px_rgba(16,185,129,0.4)] hover:scale-105 transition-all"
                >
                  <Download className="w-4 h-4" />
                  <span>Install Package</span>
                </button>
              )}
            </div>
          </div>
        </div>
      ))}
    </>
  );
};
