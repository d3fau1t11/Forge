import { useCallback, useEffect, useRef, useState } from 'react';
import { TerminalLog } from '../types';
import { apiService } from '../services/api';

/**
 * Map persisted `TerminalCommandModel` rows (from GET /api/terminal/history) into
 * `TerminalLog` entries. These are operator-typed commands — distinct from the
 * agent/swarm `ToolExecutionModel` rows that arrive through the `logs` prop as
 * `EXECUTION` entries.
 */
export function mapTerminalHistoryToLogs(history: any[]): TerminalLog[] {
  if (!Array.isArray(history)) return [];
  return history.map((entry: any) => ({
    id: entry.id,
    timestamp: entry.created_at ? new Date(entry.created_at).toLocaleTimeString() : '--:--:--',
    type: 'FORGE TOOL EXECUTION' as const,
    command: entry.command,
    output: entry.stdout || entry.stderr || '',
    exitCode: entry.exit_code ?? 0,
    duration: entry.duration_ms ? `${(entry.duration_ms / 1000).toFixed(1)}s` : '0.0s',
    privilege: 'SAFE' as const,
    agent: 'operator',
    challengeId: entry.challenge_id,
  }));
}

export interface TerminalLogsController {
  /** Operator history merged with the live agent-execution logs. */
  logs: TerminalLog[];
  isExecuting: boolean;
  /** POST the command to /api/terminal/execute, scoped to the active challenge. */
  executeCommand: (command: string) => Promise<void>;
  /** Re-fetch operator-typed history for the active challenge. */
  refreshHistory: () => Promise<void>;
  /** Clear locally buffered logs (mirrors the legacy CLEAR control). */
  clearLocalLogs: () => void;
}

/**
 * Single source of truth for "how do we fetch and merge terminal logs".
 *
 * Two backends feed the terminal and both are intentional:
 *   - `TerminalCommandModel` — operator-typed commands, fetched from
 *     GET /api/terminal/history, scoped by `activeChallengeId`.
 *   - `ToolExecutionModel` — agent/swarm-run commands, passed in as live logs
 *     by the parent (App.tsx maps GET /api/tools/executions).
 *
 * The global Terminal page and the embedded challenge-workspace Terminal tab
 * share this hook so the fetch/merge/execute behaviour cannot drift, even
 * though each renders its own UI shell around the same data.
 */
export function useTerminalLogs(
  liveLogs: TerminalLog[],
  activeChallengeId?: string,
  historyLimit: number = 200
): TerminalLogsController {
  const [historyLogs, setHistoryLogs] = useState<TerminalLog[]>([]);
  const [userLogs, setUserLogs] = useState<TerminalLog[]>([]);
  const [isExecuting, setIsExecuting] = useState(false);
  const executingRef = useRef(false);

  const refreshHistory = useCallback(async () => {
    if (!activeChallengeId) return;
    try {
      const history = await apiService.getTerminalHistory(activeChallengeId, historyLimit);
      setHistoryLogs(mapTerminalHistoryToLogs(history));
    } catch (err) {
      console.warn('Failed to load terminal history:', err);
    }
  }, [activeChallengeId, historyLimit]);

  // Load operator-typed history on mount and whenever the active challenge changes.
  useEffect(() => {
    if (!activeChallengeId) {
      setHistoryLogs([]);
      return;
    }
    refreshHistory();
  }, [activeChallengeId, refreshHistory]);

  const executeCommand = useCallback(async (command: string) => {
    const cmd = (command || '').trim();
    if (!cmd || executingRef.current) return;

    executingRef.current = true;
    setIsExecuting(true);
    let succeeded = false;
    try {
      await apiService.executeTerminalCommand(cmd, activeChallengeId);
      succeeded = true;
    } catch (err) {
      console.warn('Fallback execution via API error:', err);
    } finally {
      executingRef.current = false;
      setIsExecuting(false);
    }

    // Re-sync so the operator-typed command appears in the timeline.
    if (succeeded) await refreshHistory();
  }, [activeChallengeId, refreshHistory]);

  // Combine operator history (older) with live agent-execution logs (newer).
  const logs = [...historyLogs, ...(liveLogs && liveLogs.length > 0 ? liveLogs : userLogs)];
  const clearLocalLogs = useCallback(() => setUserLogs([]), []);

  return { logs, isExecuting, executeCommand, refreshHistory, clearLocalLogs };
}
