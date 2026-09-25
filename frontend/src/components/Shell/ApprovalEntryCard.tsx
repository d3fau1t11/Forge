import React, { useRef, useState } from 'react';
import {
  ShieldAlert,
  Terminal,
  X,
  CheckCircle2,
  XCircle,
  Loader2,
  Key,
  Lock,
  Package,
  RefreshCw,
} from 'lucide-react';
import { soundEngine } from '../../utils/soundEngine';

/**
 * Why a privileged command is being requested.  Carried from the backend gate's
 * ``context`` field (see backend/privilege/gate.require_approval) so the operator can
 * tell an auto-acquired tool install or a retry of something they already refused apart
 * from a bare command.  Absent/unknown kinds fall back to the generic privilege card.
 */
export type ApprovalRequestKind = 'tool_install' | 'capability_gap_retry' | string;

export interface CommandApprovalRequest {
  requestId: string;
  challengeId?: string;
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

  // ── Context (presentation only; never changes what executes) ──────────────────
  requestKind?: ApprovalRequestKind | null;
  capability?: string;
  provider?: string;
  method?: string;
  installCommand?: string;
  reason?: string;
  target?: string;
  previousDecision?: string | null;
}

/** A decision that already happened — logged, never clickable. */
export interface ResolvedApproval extends CommandApprovalRequest {
  decision: string | null;
  approved: boolean;
  resolvedAt: string;
}

const KIND_LABELS: Record<string, string> = {
  tool_install: 'TOOL INSTALL APPROVAL REQUIRED',
  capability_gap_retry: 'CAPABILITY-GAP RETRY',
};

const KIND_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  tool_install: Package,
  capability_gap_retry: RefreshCw,
};

/** Human-readable label for a resolved row, in the operator's vocabulary. */
export function resolutionLabel(decision: string | null, approved: boolean): string {
  if (approved) return decision === 'auto-approved' ? 'AUTO-APPROVED' : 'APPROVED';
  return decision === null ? 'NO DECISION' : 'DENIED';
}

/**
 * Map a backend gate payload (APPROVAL_REQUIRED / APPROVAL_RESOLVED, or an item from
 * GET /approvals/pending) onto a request object.
 *
 * One mapper for all three sources so a request looks identical whether it arrived over
 * the socket just now or was refetched from the registry after a page load — the wire
 * field names (snake_case `context.keys`) differ from the UI's, and doing this in two
 * places is how they drift.
 */
export function approvalFromPayload(data: any): CommandApprovalRequest {
  const ctx = data?.context || {};
  return {
    requestId: data?.request_id,
    challengeId: data?.challenge_id ?? undefined,
    runId: data?.run_id ?? undefined,
    agentId: data?.agent_id || 'unknown',
    command: data?.command || '',
    privilegeLevel: data?.privilege_level || 'PRIVILEGED',
    requiresSudo: !!data?.requires_sudo,
    decisionRequired: data?.decision_required !== false,
    timestamp: data?.timestamp || new Date().toLocaleTimeString(),
    requestKind: ctx.request_kind ?? null,
    capability: ctx.capability,
    provider: ctx.provider,
    method: ctx.method,
    installCommand: ctx.install_command,
    // Tool installs explain themselves with `reason`; capability gaps record the denial
    // as `denied_reason`. Either way the operator sees why the request was raised.
    reason: ctx.reason ?? ctx.denied_reason,
    target: ctx.target,
    previousDecision: ctx.previous_decision ?? null,
  };
}

/** Map an APPROVAL_RESOLVED payload onto a logged (non-actionable) entry. */
export function resolvedFromPayload(data: any): ResolvedApproval {
  return {
    ...approvalFromPayload(data),
    decision: data?.decision ?? null,
    approved: !!data?.approved,
    resolvedAt: new Date().toLocaleTimeString(),
  };
}

interface ContextRowProps {
  label: string;
  value?: string | null;
  mono?: boolean;
}

const ContextRow: React.FC<ContextRowProps> = ({ label, value, mono }) => {
  if (!value) return null;
  return (
    <div className="flex items-start justify-between gap-3 py-1 border-b border-slate-800/60 last:border-b-0">
      <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-500 flex-shrink-0">
        {label}
      </span>
      <span className={`text-[11px] text-slate-200 text-right break-all ${mono ? 'font-mono text-emerald-300' : ''}`}>
        {value}
      </span>
    </div>
  );
};

/**
 * The detail block that makes a request decidable.  Rendered only when the backend
 * supplied context, so a plain privileged command looks exactly as it always has.
 */
const RequestContext: React.FC<{ request: CommandApprovalRequest }> = ({ request }) => {
  const isInstall = request.requestKind === 'tool_install';
  const isRetry = request.requestKind === 'capability_gap_retry';
  if (!isInstall && !isRetry) return null;

  return (
    <div className="mb-4 rounded bg-obsidian-900 border border-slate-800 px-3 py-2">
      <span className="block text-[10px] uppercase font-semibold tracking-wider text-slate-400 mb-1">
        Request Context
      </span>
      {isInstall && (
        <>
          <ContextRow label="Capability" value={request.capability} mono />
          <ContextRow label="Provider" value={request.provider} mono />
          <ContextRow label="Method" value={request.method} />
          <ContextRow label="Install Command" value={request.installCommand} mono />
          <ContextRow label="Reason" value={request.reason} />
        </>
      )}
      {isRetry && (
        <>
          <ContextRow label="Capability" value={request.capability} mono />
          <ContextRow label="Challenge" value={request.challengeId} />
          <ContextRow label="Target" value={request.target} mono />
          <ContextRow
            label="Previously"
            value={
              request.previousDecision
                ? `${String(request.previousDecision).toUpperCase()} (${request.privilegeLevel})`
                : `DENIED (${request.privilegeLevel})`
            }
          />
          <ContextRow label="Reason" value={request.reason} />
        </>
      )}
    </div>
  );
};

interface ApprovalEntryCardProps {
  request: CommandApprovalRequest;
  /** Omitted for resolved/logged rows, which are read-only by construction. */
  onRespond?: (requestId: string, decision: 'approve' | 'deny', sudoPassword?: string) => Promise<void>;
  onDismiss?: (requestId: string) => void;
  /** Present ⇒ already-resolved row: no Approve/Deny, no credential field. */
  resolution?: { decision: string | null; approved: boolean; resolvedAt: string };
}

/**
 * One privileged-action approval card.
 *
 * Rendered by BOTH the global interrupt modal and the Operator Approvals panel on
 * /tools, so a tool-install or capability-gap retry is approved through exactly the same
 * control as any other privileged command — one card, one interaction, one backend
 * endpoint (POST /approvals/{id}/respond).
 *
 * The sudo password lives in THIS component's local state and nowhere else: it is never
 * lifted to a store, never persisted, and is discarded when the card unmounts (which,
 * for a pending card, is the moment the request resolves).
 */
export const ApprovalEntryCard: React.FC<ApprovalEntryCardProps> = ({
  request,
  onRespond,
  onDismiss,
  resolution,
}) => {
  const isResolved = resolution !== undefined;
  const [sudoPassword, setSudoPassword] = useState('');
  const [sudoError, setSudoError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<'approve' | 'deny' | null>(null);
  const passwordRef = useRef<HTMLInputElement | null>(null);

  const isDangerous = request.privilegeLevel === 'DANGEROUS';
  const isPrivileged = request.privilegeLevel === 'PRIVILEGED';
  // Auto-approval mode + sudo command: execution is already authorized, so this is a
  // credential prompt, not a yes/no decision — render the reduced UI.
  const isCredentialOnly = request.decisionRequired === false;
  const KindIcon = request.requestKind ? KIND_ICONS[request.requestKind] : undefined;
  const title = (request.requestKind && KIND_LABELS[request.requestKind]) || 'COMMAND PRIVILEGE APPROVAL REQUIRED';
  const accentRose = isDangerous && !isResolved;
  const accentEmerald = isResolved && resolution!.approved;
  const accentSlate = isResolved && !resolution!.approved;

  const handleAction = async (decision: 'approve' | 'deny', requiresSudo: boolean) => {
    if (!onRespond) return;

    // Validate: if approving a sudo command, a password must be entered.
    if (decision === 'approve' && requiresSudo && !sudoPassword.trim()) {
      setSudoError('Sudo password is required to approve this command.');
      return;
    }
    setSudoError(null);

    try {
      if (decision === 'approve') soundEngine.playSuccess();
      else soundEngine.playClick();
    } catch {
      // Audio fallback
    }

    setSubmitting(decision);

    // Capture the password before clearing it, then wipe it from local state AND the
    // DOM input immediately so it is not retained after submission. Only on approve —
    // deny never needs it.
    const capturedPassword = decision === 'approve' && requiresSudo ? sudoPassword || undefined : undefined;
    if (requiresSudo) {
      setSudoPassword('');
      if (passwordRef.current) passwordRef.current.value = '';
    }

    try {
      await onRespond(request.requestId, decision, capturedPassword);
    } finally {
      setSubmitting(null);
    }
  };

  const containerAccent = accentRose
    ? 'border-rose-500/80 shadow-[0_0_60px_rgba(244,63,94,0.35)]'
    : accentEmerald
    ? 'border-emerald-500/70 shadow-[0_0_40px_rgba(16,185,129,0.22)]'
    : accentSlate
    ? 'border-slate-600/80 shadow-[0_0_30px_rgba(15,23,42,0.5)]'
    : 'border-amber-500/80 shadow-[0_0_60px_rgba(245,158,11,0.35)]';

  const headerAccent = accentRose
    ? 'border-rose-500/40 bg-rose-950/20'
    : accentEmerald
    ? 'border-emerald-500/40 bg-emerald-950/20'
    : accentSlate
    ? 'border-slate-700/60 bg-slate-900/30'
    : 'border-amber-500/40 bg-amber-950/20';

  const titleAccent = accentRose
    ? 'text-rose-400'
    : accentEmerald
    ? 'text-emerald-400'
    : accentSlate
    ? 'text-slate-400'
    : 'text-amber-400';

  const iconAccent = accentRose
    ? 'bg-rose-500/20 border-rose-500/60 text-rose-400'
    : accentEmerald
    ? 'bg-emerald-500/20 border-emerald-500/60 text-emerald-400'
    : accentSlate
    ? 'bg-slate-800/60 border-slate-700 text-slate-400'
    : 'bg-amber-500/20 border-amber-500/60 text-amber-400';

  return (
    <div
      className={`w-full bg-obsidian-950 border-2 ${containerAccent} rounded-xl p-5 font-mono text-slate-100 relative cyber-corner`}
    >
      {/* Header */}
      <div className={`flex items-center justify-between border-b ${headerAccent} pb-3 mb-4 p-3 rounded`}>
        <div className="flex items-center space-x-3">
          <div className={`w-11 h-11 rounded-lg ${iconAccent} border flex items-center justify-center ${isResolved ? '' : 'animate-pulse'}`}>
            {KindIcon ? <KindIcon className="w-6 h-6" /> : <ShieldAlert className="w-6 h-6" />}
          </div>
          <div>
            <h2 className={`text-base font-display font-bold tracking-wider ${titleAccent}`}>{title}</h2>
            <p className="text-[11px] text-slate-300 font-semibold tracking-wide uppercase flex items-center space-x-2">
              <span
                className={`w-2 h-2 rounded-full ${
                  accentRose ? 'bg-rose-400' : accentEmerald ? 'bg-emerald-400' : accentSlate ? 'bg-slate-500' : 'bg-amber-400'
                } ${isResolved ? '' : 'animate-ping'}`}
              ></span>
              <span>
                {isResolved
                  ? `${resolutionLabel(resolution!.decision, resolution!.approved)} · ${resolution!.resolvedAt}`
                  : isCredentialOnly
                  ? 'Auto-approve mode: only the sudo credential is required to continue'
                  : 'Operator authorization required before execution'}
              </span>
            </p>
          </div>
        </div>

        {!isResolved && (
          <button
            onClick={() => (onDismiss ? onDismiss(request.requestId) : handleAction('deny', request.requiresSudo))}
            disabled={!!submitting}
            className="p-1.5 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50"
            title="Deny and dismiss"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Metadata Badges */}
      <div className="grid grid-cols-2 gap-3 mb-4 text-xs">
        <div className="p-2.5 rounded bg-obsidian-900 border border-slate-800">
          <span className="text-slate-400 block text-[10px] uppercase font-semibold tracking-wider">Agent ID</span>
          <span className="text-cyan-400 font-bold tracking-wide">{request.agentId}</span>
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
            {request.privilegeLevel}
          </span>
        </div>
      </div>

      {/* Why this request exists */}
      <RequestContext request={request} />

      {/* Monospace Command Block */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-semibold text-slate-300 flex items-center space-x-1.5">
            <Terminal className="w-3.5 h-3.5 text-cyan-400" />
            <span>Proposed Command:</span>
          </span>
        </div>
        <pre className="p-3 bg-obsidian-900/90 rounded border border-slate-700/60 text-emerald-400 font-mono text-xs overflow-x-auto select-text whitespace-pre-wrap break-all max-h-48 leading-relaxed shadow-inner">
          <code>{request.command}</code>
        </pre>
      </div>

      {/* Sudo Password Field — shown whenever the command needs sudo. In auto
          (credential-only) mode it IS the whole interaction, so it is always
          rendered there rather than being conditional on an Approve click. */}
      {!isResolved && (request.requiresSudo || isCredentialOnly) && (
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
              id={`sudo-password-input-${request.requestId}`}
              type="password"
              autoComplete="current-password"
              disabled={!!submitting}
              placeholder="Enter sudo password…"
              ref={passwordRef}
              value={sudoPassword}
              onChange={(e) => {
                // Store only in LOCAL state — not in any global store or persistent storage.
                setSudoPassword(e.target.value);
                if (sudoError) setSudoError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !submitting) {
                  handleAction('approve', request.requiresSudo || isCredentialOnly);
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
            <p role="alert" className="mt-1.5 text-[11px] font-semibold text-rose-400 flex items-center space-x-1.5">
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
      {!isResolved && (
        <div className="flex items-center justify-end space-x-3 pt-3 border-t border-slate-800">
          {isCredentialOnly ? (
            <>
              {/* Cancel submits an explicit DENY with no password — it must never
                  be turned into an empty-password approval by the backend. */}
              <button
                type="button"
                onClick={() => handleAction('deny', true)}
                disabled={!!submitting}
                className="px-4 py-2 text-xs font-semibold rounded bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 border border-rose-600/50 hover:border-rose-500 transition-all flex items-center space-x-1.5 disabled:opacity-50"
              >
                {submitting === 'deny' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
                <span>Cancel</span>
              </button>
              <button
                type="button"
                onClick={() => handleAction('approve', true)}
                disabled={!!submitting}
                className="px-5 py-2 text-xs font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-slate-950 hover:text-black border border-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.4)] transition-all flex items-center space-x-1.5 disabled:opacity-50"
              >
                {submitting === 'approve' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                <span>Continue</span>
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => handleAction('deny', request.requiresSudo)}
                disabled={!!submitting}
                className="px-4 py-2 text-xs font-semibold rounded bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 border border-rose-600/50 hover:border-rose-500 transition-all flex items-center space-x-1.5 disabled:opacity-50"
              >
                {submitting === 'deny' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
                <span>Deny</span>
              </button>
              <button
                type="button"
                onClick={() => handleAction('approve', request.requiresSudo)}
                disabled={!!submitting}
                className="px-5 py-2 text-xs font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-slate-950 hover:text-black border border-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.4)] transition-all flex items-center space-x-1.5 disabled:opacity-50"
              >
                {submitting === 'approve' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                <span>Approve &amp; Execute</span>
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
};
