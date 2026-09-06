/**
 * Utility functions for robust duration and UTC timestamp parsing across Forge.
 */

/**
 * Parses SQLite datetime / ISO strings safely as UTC milliseconds.
 */
export const parseUtcMs = (dateStr?: string | null): number | null => {
  if (!dateStr || typeof dateStr !== 'string') return null;
  let s = dateStr.trim();
  if (!s) return null;

  // Format "YYYY-MM-DD HH:MM:SS.ffffff" -> "YYYY-MM-DDTHH:MM:SS.ffffffZ"
  if (s.includes(' ') && !s.includes('T')) {
    s = s.replace(' ', 'T');
  }
  if (!s.endsWith('Z') && !s.includes('+') && !s.includes('-0') && !s.includes('-1')) {
    s += 'Z';
  }

  const ms = new Date(s).getTime();
  return isNaN(ms) ? null : ms;
};

/**
 * Computes exact elapsed seconds for a challenge based on startedAt, completedAt, status, and durationSeconds.
 */
export const computeElapsedSeconds = (
  startedAt?: string | null,
  completedAt?: string | null,
  durationSeconds?: number | null,
  status?: string
): number => {
  const startMs = parseUtcMs(startedAt);
  const endMs = parseUtcMs(completedAt);

  if (status === 'RUNNING') {
    if (startMs) {
      const nowMs = Date.now();
      return Math.max(0, Math.floor((nowMs - startMs) / 1000));
    }
    if (durationSeconds && durationSeconds > 0) {
      return durationSeconds;
    }
    return 0;
  }

  if (status === 'COMPLETED' || status === 'FAILED' || status === 'AWAITING_FLAG' || status === 'PAUSED') {
    if (durationSeconds && durationSeconds > 0) {
      return durationSeconds;
    }
    if (startMs && endMs && endMs >= startMs) {
      return Math.floor((endMs - startMs) / 1000);
    }
    if (startMs) {
      return Math.max(0, Math.floor((Date.now() - startMs) / 1000));
    }
  }

  return durationSeconds || 0;
};

/**
 * Formats total seconds into HH:MM:SS format.
 */
export const formatDuration = (totalSeconds: number): string => {
  if (isNaN(totalSeconds) || totalSeconds < 0) totalSeconds = 0;
  const hrs = Math.floor(totalSeconds / 3600);
  const mins = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
};
