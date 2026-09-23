import React, { useState, useEffect, useRef } from 'react';
import { ShieldAlert, Terminal, X, CheckCircle2, XCircle, Loader2, Key, Lock } from 'lucide-react';
import { soundEngine } from '../../utils/soundEngine';

export interface CommandApprovalRequest {
  requestId: string;
  challengeId: string;
  runId?: string;
  agentId: string;
  command: string;
  privilegeLevel: string; // "PRIVILEGED" | "DANGEROUS" | etc.
  requiresSudo: boolean;
  /**
   * false only when FORGE runs in auto-approval mode and the command needs sudo:
   * execution is already authorized, so the operator is asked for the credential
   * alone — no Approve/Deny decision. Absent/true keeps the manual-mode UI.
   */
  decisionRequired?: boolean;
  timestamp?: string;
}

interface CommandApprovalModalProps {
  requests: CommandApprovalRequest[];
  onRespond: (requestId: string, decision: 'approve' | 'deny', sudoPassword?: string) => Promise<void>;
  onDismiss?: (requestId: string) => void;
}

export const CommandApprovalModal: React.FC<CommandApprovalModalProps> = ({
  requests,
  onRespond,
  onDismiss,
}) => {
  const [submittingIds, setSubmittingIds] = useState<{ [key: string]: 'approve' | 'deny' | null }>({});
  // Per-request local password state — kept ONLY in local state, never in
  // Redux / localStorage / sessionStorage / any wider scope.
  const [sudoPasswords, setSudoPasswords] = useState<{ [requestId: string]: string }>({});
  const [sudoErrors, setSudoErrors] = useState<{ [requestId: string]: string }>({});
  // Ref map for password inputs so we can clear the DOM value as a belt-and-suspenders measure.
  const passwordRefs = useRef<{ [requestId: string]: HTMLInputElement | null }>({});

  useEffect(() => {
    if (requests.length > 0) {
      try {
        soundEngine.playWarning();
      } catch (e) {
        // Audio fallback
      }
    }
  }, [requests.length]);

  // Clean up password state for requests that are no longer pending.
  useEffect(() => {
    const activeIds = new Set(requests.map((r) => r.requestId));
    setSudoPasswords((prev) => {
      const next = { ...prev };
      for (const id of Object.keys(next)) {
        if (!activeIds.has(id)) delete next[id];
      }
      return next;
    });
    setSudoErrors((prev) => {
      const next = { ...prev };
      for (const id of Object.keys(next)) {
        if (!activeIds.has(id)) delete next[id];
      }
      return next;
    });
  }, [requests]);

  if (requests.length === 0) return null;

  const handleAction = async (requestId: string, decision: 'approve' | 'deny', requiresSudo: boolean) => {
    // Validate: if approving a sudo command, a password must be entered.
    if (decision === 'approve' && requiresSudo) {
      const pw = sudoPasswords[requestId] || '';
      if (!pw.trim()) {
        setSudoErrors((prev) => ({ ...prev, [requestId]: 'Sudo password is required to approve this command.' }));
        return;
      }
    }

    // Clear any previous error.
    setSudoErrors((prev) => {
      const next = { ...prev };
      delete next[requestId];
      return next;
    });

    try {
      if (decision === 'approve') {
        soundEngine.playSuccess();
      } else {
        soundEngine.playClick();
      }
    } catch (e) {
      // Audio fallback
    }

    setSubmittingIds((prev) => ({ ...prev, [requestId]: decision }));

    // Capture the password from local state before clearing it.
    // Only pass it on approve; deny never needs it.
    const capturedPassword = (decision === 'approve' && requiresSudo)
      ? (sudoPasswords[requestId] || undefined)
      : undefined;

    // Immediately clear the password from local state AND the DOM input
    // so it is not retained in component state after submission.
    if (requiresSudo) {
      setSudoPasswords((prev) => {
        const next = { ...prev };
        delete next[requestId];
        return next;
      });
      const inputEl = passwordRefs.current[requestId];
      if (inputEl) {
        inputEl.value = '';
      }
    }

    try {
      await onRespond(requestId, decision, capturedPassword);
    } finally {
      setSubmittingIds((prev) => {
        const next = { ...prev };
        delete next[requestId];
        return next;
      });
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 select-none animate-fadeIn">
      {requests.map((req) => {
        const isDangerous = req.privilegeLevel === 'DANGEROUS';
        const isPrivileged = req.privilegeLevel === 'PRIVILEGED';
        const isSubmitting = !!submittingIds[req.requestId];
        const sudoError = sudoErrors[req.requestId];
        // Auto-approval mode + sudo command: execution is already authorized, so this
        // is a credential prompt, not a yes/no decision — render the reduced UI.
        const isCredentialOnly = req.decisionRequired === false;

        return (
          <div
            key={req.requestId}
            className={`w-full max-w-2xl bg-obsidian-950 border-2 ${
              isDangerous
                ? 'border-rose-500/80 shadow-[0_0_60px_rgba(244,63,94,0.35)]'
                : 'border-amber-500/80 shadow-[0_0_60px_rgba(245,158,11,0.35)]'
            } rounded-xl p-6 font-mono text-slate-100 relative cyber-corner`}
          >
            {/* Header */}
            <div
              className={`flex items-center justify-between border-b ${
                isDangerous ? 'border-rose-500/40 bg-rose-950/20' : 'border-amber-500/40 bg-amber-950/20'
              } pb-4 mb-4 p-3 rounded`}
            >
              <div className="flex items-center space-x-3">
                <div
                  className={`w-11 h-11 rounded-lg ${
                    isDangerous
                      ? 'bg-rose-500/20 border-rose-500/60 text-rose-400 shadow-[0_0_20px_rgba(244,63,94,0.4)]'
                      : 'bg-amber-500/20 border-amber-500/60 text-amber-400 shadow-[0_0_20px_rgba(245,158,11,0.4)]'
                  } border flex items-center justify-center animate-pulse`}
                >
                  <ShieldAlert className="w-6 h-6" />
                </div>
                <div>
                  <h2
                    className={`text-lg font-display font-bold tracking-wider ${
                      isDangerous ? 'text-rose-400' : 'text-amber-400'
                    }`}
                  >
                    COMMAND PRIVILEGE APPROVAL REQUIRED
                  </h2>
                  <p className="text-[11px] text-slate-300 font-semibold tracking-wide uppercase flex items-center space-x-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        isDangerous ? 'bg-rose-400' : 'bg-amber-400'
                      } animate-ping`}
                    ></span>
                    <span>
                      {isCredentialOnly
                        ? 'Auto-approve mode: only the sudo credential is required to continue'
                        : 'Operator authorization required before execution'}
                    </span>
                  </p>
                </div>
              </div>

              <button
                onClick={() => (onDismiss ? onDismiss(req.requestId) : handleAction(req.requestId, 'deny', req.requiresSudo))}
                disabled={isSubmitting}
                className="p-1.5 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50"
                title="Deny and dismiss"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Metadata Badges */}
            <div className="grid grid-cols-2 gap-3 mb-4 text-xs">
              <div className="p-2.5 rounded bg-obsidian-900 border border-slate-800">
                <span className="text-slate-400 block text-[10px] uppercase font-semibold tracking-wider">Agent ID</span>
                <span className="text-cyan-400 font-bold tracking-wide">{req.agentId}</span>
              </div>
              <div className="p-2.5 rounded bg-obsidian-900 border border-slate-800">
                <span className="text-slate-400 block text-[10px] uppercase font-semibold tracking-wider">Privilege Level</span>
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold tracking-wider uppercase border ${
                    isDangerous
                      ? 'bg-rose-500/20 text-rose-300 border-rose-500/60 shadow-[0_0_10px_rgba(244,63,94,0.3)]'
                      : isPrivileged
                      ? 'bg-amber-500/20 text-amber-300 border-amber-500/60 shadow-[0_0_10px_rgba(245,158,11,0.3)]'
                      : 'bg-slate-500/20 text-slate-300 border-slate-500/60'
                  }`}
                >
                  {req.privilegeLevel}
                </span>
              </div>
            </div>

            {/* Monospace Command Block */}
            <div className="mb-4">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-semibold text-slate-300 flex items-center space-x-1.5">
                  <Terminal className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Proposed Command:</span>
                </span>
              </div>
              <pre className="p-3 bg-obsidian-900/90 rounded border border-slate-700/60 text-emerald-400 font-mono text-xs overflow-x-auto select-text whitespace-pre-wrap break-all max-h-48 leading-relaxed shadow-inner">
                <code>{req.command}</code>
              </pre>
            </div>

            {/* Sudo Password Field — shown whenever the command needs sudo. In auto
                (credential-only) mode it IS the whole interaction, so it is always
                rendered there rather than being conditional on an Approve click. */}
            {(req.requiresSudo || isCredentialOnly) && (
              <div className="mb-4">
                <div className="flex items-center space-x-2 mb-2">
                  <Key className="w-4 h-4 text-amber-400 flex-shrink-0" />
                  <span className="text-xs font-semibold text-amber-300 uppercase tracking-wider">
                    Sudo Password Required
                  </span>
                </div>
                {isCredentialOnly && (
                  <p className="mb-2 text-[11px] text-amber-200/90 leading-relaxed">
                    Auto-approve mode is active — this command is already authorized to run.
                    Supply the sudo password to continue, or cancel to deny it.
                  </p>
                )}
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
                  <input
                    id={`sudo-password-input-${req.requestId}`}
                    type="password"
                    autoComplete="current-password"
                    disabled={isSubmitting}
                    placeholder="Enter sudo password…"
                    ref={(el) => { passwordRefs.current[req.requestId] = el; }}
                    value={sudoPasswords[req.requestId] || ''}
                    onChange={(e) => {
                      const val = e.target.value;
                      // Store only in LOCAL state — not in any global store or persistent storage.
                      setSudoPasswords((prev) => ({ ...prev, [req.requestId]: val }));
                      // Clear error as user types.
                      if (sudoErrors[req.requestId]) {
                        setSudoErrors((prev) => { const n = { ...prev }; delete n[req.requestId]; return n; });
                      }
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !isSubmitting) {
                        handleAction(req.requestId, 'approve', req.requiresSudo || isCredentialOnly);
                      }
                    }}
                    className={`w-full pl-9 pr-3 py-2.5 rounded text-xs font-mono bg-obsidian-900 border ${
                      sudoError
                        ? 'border-rose-500 focus:border-rose-400 ring-1 ring-rose-500/40'
                        : 'border-slate-700 focus:border-amber-500/60 focus:ring-1 focus:ring-amber-500/20'
                    } text-slate-100 placeholder-slate-600 outline-none transition-all disabled:opacity-50`}
                  />
                </div>
                {sudoError && (
                  <p
                    role="alert"
                    className="mt-1.5 text-[11px] font-semibold text-rose-400 flex items-center space-x-1.5"
                  >
                    <XCircle className="w-3.5 h-3.5 flex-shrink-0" />
                    <span>{sudoError}</span>
                  </p>
                )}
                <p className="mt-1.5 text-[10px] text-slate-500 leading-relaxed">
                  Password is sent encrypted over HTTPS and is never stored or logged.
                  It is used only to authenticate this single command execution.
                </p>
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex items-center justify-end space-x-3 pt-3 border-t border-slate-800">
              {isCredentialOnly ? (
                <>
                  {/* Cancel submits an explicit DENY with no password — it must never
                      be turned into an empty-password approval by the backend. */}
                  <button
                    type="button"
                    onClick={() => handleAction(req.requestId, 'deny', true)}
                    disabled={isSubmitting}
                    className="px-4 py-2 text-xs font-semibold rounded bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 border border-rose-600/50 hover:border-rose-500 transition-all flex items-center space-x-1.5 disabled:opacity-50"
                  >
                    {submittingIds[req.requestId] === 'deny' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5" />
                    )}
                    <span>Cancel</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleAction(req.requestId, 'approve', true)}
                    disabled={isSubmitting}
                    className="px-5 py-2 text-xs font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-slate-950 hover:text-black border border-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.4)] transition-all flex items-center space-x-1.5 disabled:opacity-50"
                  >
                    {submittingIds[req.requestId] === 'approve' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <CheckCircle2 className="w-3.5 h-3.5" />
                    )}
                    <span>Continue</span>
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => handleAction(req.requestId, 'deny', req.requiresSudo)}
                    disabled={isSubmitting}
                    className="px-4 py-2 text-xs font-semibold rounded bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 border border-rose-600/50 hover:border-rose-500 transition-all flex items-center space-x-1.5 disabled:opacity-50"
                  >
                    {submittingIds[req.requestId] === 'deny' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5" />
                    )}
                    <span>Deny</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleAction(req.requestId, 'approve', req.requiresSudo)}
                    disabled={isSubmitting}
                    className="px-5 py-2 text-xs font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-slate-950 hover:text-black border border-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.4)] transition-all flex items-center space-x-1.5 disabled:opacity-50"
                  >
                    {submittingIds[req.requestId] === 'approve' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <CheckCircle2 className="w-3.5 h-3.5" />
                    )}
                    <span>Approve &amp; Execute</span>
                  </button>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};
