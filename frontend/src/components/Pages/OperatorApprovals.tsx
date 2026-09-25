import React from 'react';
import { ShieldCheck, Zap, RefreshCw, Package, Inbox } from 'lucide-react';
import {
  ApprovalEntryCard,
  CommandApprovalRequest,
  ResolvedApproval,
  resolutionLabel,
} from '../Shell/ApprovalEntryCard';

// Resolved entries are a log, not a state to act on — cap what we retain so a long run
// cannot grow this list without bound.
const MAX_RESOLVED_SHOWN = 50;

const KIND_BADGES: Record<string, { label: string; Icon: React.ComponentType<{ className?: string }>; className: string }> = {
  tool_install: {
    label: 'INSTALL',
    Icon: Package,
    className: 'bg-cyan-950 border-cyan-800 text-cyan-300',
  },
  capability_gap_retry: {
    label: 'CAP-GAP RETRY',
    Icon: RefreshCw,
    className: 'bg-amber-950 border-amber-800 text-amber-300',
  },
};

interface OperatorApprovalsProps {
  pending: CommandApprovalRequest[];
  resolved: ResolvedApproval[];
  onRespond: (requestId: string, decision: 'approve' | 'deny', sudoPassword?: string) => Promise<void>;
  onDismiss?: (requestId: string) => void;
}

/**
 * Operator Approvals — the persistent home for privileged-action decisions.
 *
 * Pending entries render through the SAME ApprovalEntryCard used by the global interrupt
 * modal, so an auto-acquired tool install or a capability-gap retry is approved through
 * exactly the control that every other privileged command uses. Resolved entries — which
 * includes anything a challenge's "auto" mode resolved without asking — appear below as
 * logged rows: they carry no buttons, because there is no decision left to make.
 *
 * This panel adds no approval pathway of its own. Every button here calls the same
 * onRespond → POST /approvals/{request_id}/respond.
 */
export const OperatorApprovals: React.FC<OperatorApprovalsProps> = ({
  pending,
  resolved,
  onRespond,
  onDismiss,
}) => {
  const shownResolved = resolved.slice(0, MAX_RESOLVED_SHOWN);

  return (
    <div className="bg-[#0b1019] border border-amber-500/40 rounded-lg p-4 space-y-3 shadow-[0_0_20px_rgba(245,158,11,0.08)]">
      <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
        <div className="flex items-center space-x-2">
          <ShieldCheck className="w-4 h-4 text-amber-400" />
          <h2 className="text-xs font-bold tracking-wider text-slate-100 uppercase">Operator Approvals</h2>
        </div>
        <div className="flex items-center space-x-2 text-[10px]">
          {pending.length > 0 ? (
            <span className="px-2 py-0.5 rounded bg-amber-950 border border-amber-700 text-amber-300 font-bold animate-pulse">
              {pending.length} PENDING
            </span>
          ) : (
            <span className="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-500 font-bold">
              NONE PENDING
            </span>
          )}
          {shownResolved.length > 0 && (
            <span className="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400">
              {shownResolved.length} LOGGED
            </span>
          )}
        </div>
      </div>

      {/* Pending — interactive, one card per request, same control as the modal. */}
      {pending.length > 0 && (
        <div className="space-y-3">
          {pending.map((req) => (
            <ApprovalEntryCard key={req.requestId} request={req} onRespond={onRespond} onDismiss={onDismiss} />
          ))}
        </div>
      )}

      {/* Resolved — logged rows. Auto-mode decisions land here without ever being pending. */}
      {shownResolved.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center space-x-2 pt-1">
            <Zap className="w-3 h-3 text-slate-500" />
            <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-500">
              Resolved
            </span>
          </div>
          <div className="space-y-1 max-h-72 overflow-y-auto">
            {shownResolved.map((entry) => {
              const badge = entry.requestKind ? KIND_BADGES[entry.requestKind] : undefined;
              return (
                <div
                  key={`${entry.requestId}-${entry.resolvedAt}`}
                  className="flex items-start justify-between gap-3 px-2.5 py-1.5 rounded bg-[#070b12] border border-slate-800/80 text-[11px]"
                >
                  <div className="flex items-start space-x-2 min-w-0">
                    <span
                      className={`mt-0.5 px-1.5 py-0.2 rounded border text-[9px] font-bold flex-shrink-0 ${
                        entry.approved
                          ? 'bg-emerald-950 border-emerald-800 text-emerald-400'
                          : 'bg-rose-950 border-rose-800 text-rose-400'
                      }`}
                    >
                      {resolutionLabel(entry.decision, entry.approved)}
                    </span>
                    {badge && (
                      <span className={`mt-0.5 px-1.5 py-0.2 rounded border text-[9px] font-bold flex-shrink-0 flex items-center space-x-1 ${badge.className}`}>
                        <badge.Icon className="w-2.5 h-2.5" />
                        <span>{badge.label}</span>
                      </span>
                    )}
                    <div className="min-w-0">
                      {entry.capability && (
                        <div className="text-cyan-300 font-bold truncate">{entry.capability}</div>
                      )}
                      <code className="text-slate-400 break-all line-clamp-2">{entry.command}</code>
                    </div>
                  </div>
                  <span className="text-slate-600 flex-shrink-0">{entry.resolvedAt}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Nothing pending and nothing logged — a clean empty state, not a blank box. */}
      {pending.length === 0 && shownResolved.length === 0 && (
        <div className="flex items-center space-x-2 py-3 text-[11px] text-slate-500">
          <Inbox className="w-3.5 h-3.5" />
          <span>NO APPROVAL ACTIVITY — privileged commands will appear here for review.</span>
        </div>
      )}
    </div>
  );
};
