import React, { useState, useEffect } from 'react';
import { ShieldAlert, Terminal, X, CheckCircle, Loader2, Key, AlertTriangle } from 'lucide-react';
import { soundEngine } from '../../utils/soundEngine';

export interface RootPermissionRequest {
  requestId: string;
  challengeId: string;
  challengeName: string;
  command: string;
  reason: string;
  errorSnippet?: string;
  timestamp: string;
}

interface RootPermissionModalProps {
  requests: RootPermissionRequest[];
  onApprove: (requestId: string, command: string, sudoPassword?: string) => void;
  onDismiss: (requestId: string) => void;
}

export const RootPermissionModal: React.FC<RootPermissionModalProps> = ({
  requests,
  onApprove,
  onDismiss
}) => {
  const [approvingIds, setApprovingIds] = useState<Set<string>>(new Set());
  const [sudoPasswords, setSudoPasswords] = useState<{ [key: string]: string }>({});
  const [showPasswordInputs, setShowPasswordInputs] = useState<{ [key: string]: boolean }>({});

  useEffect(() => {
    if (requests.length > 0) {
      try {
        soundEngine.playWarning();
      } catch (e) {
        // Audio fallback
      }
    }
  }, [requests.length]);

  if (requests.length === 0) return null;

  const handleApprove = (req: RootPermissionRequest) => {
    try {
      soundEngine.playSuccess();
    } catch (e) {
      // Audio fallback
    }
    setApprovingIds((prev) => new Set(prev).add(req.requestId));
    const pass = sudoPasswords[req.requestId] || undefined;
    onApprove(req.requestId, req.command, pass);
  };

  const handleReject = (req: RootPermissionRequest) => {
    try {
      soundEngine.playClick();
    } catch (e) {
      // Audio fallback
    }
    onDismiss(req.requestId);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 select-none animate-fadeIn">
      {requests.map((req) => (
        <div
          key={req.requestId}
          className="w-full max-w-xl bg-obsidian-950 border-2 border-amber-500/80 rounded-xl p-6 shadow-[0_0_60px_rgba(245,158,11,0.35)] font-mono text-slate-100 relative cyber-corner"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-amber-500/40 pb-4 mb-4 bg-obsidian-900/80 p-3 rounded">
            <div className="flex items-center space-x-3">
              <div className="w-11 h-11 rounded-lg bg-amber-500/20 border border-amber-500/60 flex items-center justify-center text-amber-400 shadow-[0_0_20px_rgba(245,158,11,0.4)] animate-pulse">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div>
                <h2 className="text-lg font-display font-bold tracking-wider text-amber-400">
                  ROOT PRIVILEGE AUTHORIZATION
                </h2>
                <p className="text-[11px] text-amber-300/80 font-semibold tracking-wide uppercase flex items-center space-x-1.5">
                  <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>
                  <span>Autonomous Agent requested elevated superuser permissions</span>
                </p>
              </div>
            </div>

            <button
              onClick={() => handleReject(req)}
              className="p-1.5 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
              title="Deny privilege request"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Body Details */}
          <div className="space-y-4 mb-5">
            <div className="flex items-center space-x-2 text-xs text-slate-300">
              <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
              <span>
                Challenge: <strong className="text-slate-100">{req.challengeName}</strong>
              </span>
            </div>

            {/* Command Box */}
            <div className="bg-obsidian-900 border border-slate-700/80 rounded-lg p-3">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider flex items-center space-x-1">
                  <Terminal className="w-3.5 h-3.5 text-cyber-cyan" />
                  <span>Requested Command</span>
                </span>
                <span className="text-[10px] text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/30 font-bold">
                  SUDO / ROOT REQUIRED
                </span>
              </div>
              <code className="text-xs text-cyber-cyan block overflow-x-auto p-2 bg-black/50 rounded border border-slate-800 select-text">
                {req.command.startsWith('sudo') ? req.command : `sudo ${req.command}`}
              </code>
            </div>

            {/* Error / Reason output if any */}
            {req.errorSnippet && (
              <div className="bg-obsidian-900/90 border border-red-900/40 rounded-lg p-2.5 max-h-24 overflow-y-auto">
                <span className="text-[10px] text-red-400 font-bold uppercase tracking-wider block mb-1">
                  Trigger Output / Security Error:
                </span>
                <pre className="text-[10px] text-red-300/80 whitespace-pre-wrap leading-relaxed font-mono">
                  {req.errorSnippet}
                </pre>
              </div>
            )}

            {/* Optional Sudo Password Accordion */}
            <div className="bg-obsidian-900/50 border border-slate-800 rounded-lg p-2.5">
              <button
                type="button"
                onClick={() =>
                  setShowPasswordInputs((prev) => ({
                    ...prev,
                    [req.requestId]: !prev[req.requestId]
                  }))
                }
                className="text-[11px] text-slate-400 hover:text-slate-200 flex items-center space-x-1.5 transition-colors"
              >
                <Key className="w-3.5 h-3.5 text-amber-400" />
                <span>Specify Host Sudo Password (optional if passwordless)</span>
              </button>

              {showPasswordInputs[req.requestId] && (
                <div className="mt-2 pt-2 border-t border-slate-800">
                  <input
                    type="password"
                    placeholder="Enter sudo password..."
                    value={sudoPasswords[req.requestId] || ''}
                    onChange={(e) =>
                      setSudoPasswords((prev) => ({
                        ...prev,
                        [req.requestId]: e.target.value
                      }))
                    }
                    className="w-full px-3 py-1.5 bg-obsidian-950 border border-slate-700 rounded text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-400 font-mono"
                  />
                </div>
              )}
            </div>
          </div>

          {/* Action Footer */}
          <div className="flex items-center justify-between pt-3 border-t border-slate-800">
            <button
              onClick={() => handleReject(req)}
              className="px-4 py-2.5 rounded-lg bg-obsidian-900 border border-slate-700 hover:border-slate-500 text-slate-300 hover:text-white text-xs font-bold uppercase tracking-wider transition-all flex items-center space-x-2"
            >
              <X className="w-4 h-4 text-red-400" />
              <span>Deny & Force Unprivileged Pivot</span>
            </button>

            {approvingIds.has(req.requestId) ? (
              <div className="px-5 py-2.5 rounded-lg bg-amber-500/20 border border-amber-500 text-amber-300 text-xs font-bold uppercase tracking-wider flex items-center space-x-2 animate-pulse">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Elevating & Executing...</span>
              </div>
            ) : (
              <button
                onClick={() => handleApprove(req)}
                className="px-5 py-2.5 rounded-lg bg-gradient-to-r from-amber-500 to-amber-400 hover:from-amber-400 hover:to-amber-300 text-obsidian-950 font-bold text-xs uppercase tracking-widest flex items-center space-x-2 shadow-[0_0_25px_rgba(245,158,11,0.5)] hover:scale-105 transition-all"
              >
                <CheckCircle className="w-4 h-4" />
                <span>Authorize & Run as Root</span>
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
