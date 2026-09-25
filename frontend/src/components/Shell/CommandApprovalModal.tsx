import React, { useEffect } from 'react';
import { ApprovalEntryCard, CommandApprovalRequest } from './ApprovalEntryCard';
import { soundEngine } from '../../utils/soundEngine';

// Re-exported so existing importers (App.tsx) keep working unchanged. The type itself
// now lives with the card, which is the single definition of what an approval is.
export type { CommandApprovalRequest };

interface CommandApprovalModalProps {
  requests: CommandApprovalRequest[];
  onRespond: (requestId: string, decision: 'approve' | 'deny', sudoPassword?: string) => Promise<void>;
  onDismiss?: (requestId: string) => void;
}

/**
 * The global interrupt surface for privileged-action approvals.
 *
 * It appears — anywhere in the app — the moment the backend gate is blocked waiting on
 * the operator, so a request is never missed just because the operator is on another
 * page. The identical cards are also listed persistently under Operator Approvals on
 * /tools; this modal is the "you are needed right now" view, not a second mechanism.
 */
export const CommandApprovalModal: React.FC<CommandApprovalModalProps> = ({
  requests,
  onRespond,
  onDismiss,
}) => {
  useEffect(() => {
    if (requests.length > 0) {
      try {
        soundEngine.playWarning();
      } catch {
        // Audio fallback
      }
    }
  }, [requests.length]);

  if (requests.length === 0) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 select-none animate-fadeIn overflow-y-auto">
      <div className="w-full max-w-2xl space-y-4 my-auto">
        {requests.map((req) => (
          <ApprovalEntryCard
            key={req.requestId}
            request={req}
            onRespond={onRespond}
            onDismiss={onDismiss}
          />
        ))}
      </div>
    </div>
  );
};
