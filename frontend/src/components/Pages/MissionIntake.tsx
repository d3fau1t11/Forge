/**
 * MissionIntake — Simple mission intake for FORGE.
 *
 * The operator provides:
 *   1. TARGET    — URL/IP/host/port (optional depending on challenge type)
 *   2. ARTIFACT  — One or more challenge files to upload
 *   3. OBJECTIVE — Simple text: "Find the flag", etc.
 *
 * On START FORGE, the backend infers:
 *   - challenge category
 *   - artifact type
 *   - target type
 *   - available capabilities
 *   - initial hypotheses
 *   - initial candidate actions
 *
 * The UI transitions to the mission dashboard showing live state.
 *
 * Frontend submits mission input and visualizes backend state.
 * No frontend reasoning about CTF tools.
 */

import React, { useState, useRef, useEffect } from 'react';
import { apiService } from '../../services/api';
import { Challenge } from '../../types';
import { soundEngine } from '../../utils/soundEngine';
import {
  Bot,
  X,
  ArrowLeft,
  Loader2,
  AlertTriangle,
  Paperclip,
  FileCode,
  Send
} from 'lucide-react';

/** Parsed target info */
interface ParsedTarget {
  value: string;      // raw target string (IP, URL, host:port, etc.)
  type: 'ip' | 'url' | 'host' | 'nc' | 'unknown';
}

/** Uploaded artifact */
interface UploadedArtifact {
  name: string;
  path: string;
  size: number;
}

/** Mission intake form state */
interface MissionIntakeState {
  target: string;
  targetType: 'ip' | 'url' | 'host' | 'nc' | 'unknown';
  objective: string;
  files: UploadedArtifact[];
  uploading: boolean;
  submitting: boolean;
  error: string | null;
}

/** Backend mission intake request */
interface MissionIntakeRequest {
  target_address: string;
  objective: string;
  attached_file_paths: string[];
}

/** Backend mission intake response */
interface MissionIntakeResponse {
  challenge_id: string;
  challenge_name: string;
  category: string;
  objective: string;
  target: string;
  status: string;
  progress: number;
  message: string;
  hypotheses: string[];
  initial_actions: string[];
  progress_detail: string;
  budget: { spent: number; limit: number };
  flag_status: 'UNFOUND' | 'CAPTURED' | 'VERIFYING';
  final_flag?: string;
}

/**
 * Parses a target string to infer its type.
 * Target formats:
 *   - IP: 10.10.10.10, 192.168.1.1
 *   - URL: http://example.com, https://example.com:8080/path
 *   - Host: example.com, hostname
 *   - Netcat: nc host port (e.g., "nc 10.10.10.10 9000")
 */
function parseTargetType(target: string): ParsedTarget['type'] {
  const trimmed = target.trim();

  // Netcat format: "nc host port"
  if (trimmed.toLowerCase().startsWith('nc ')) {
    return 'nc';
  }

  // URL format: starts with http:// or https://
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    return 'url';
  }

  // IP address format (dotted quad or colon-separated for IPv6)
  if (
    /^\d{1,3}(\.\d{1,3}){3}$/.test(trimmed) ||
    /^\[?[a-fA-F0-9:]+\]?$/.test(trimmed)
  ) {
    return 'ip';
  }

  // Otherwise treat as hostname
  return 'host';
}

const MISSION_INTAKE_CLS =
  'w-full bg-obsidian-950/90 border border-cyan-500/30 rounded-lg px-3.5 py-2.5 text-xs text-slate-100 ' +
  'placeholder:text-slate-500 focus:outline-none focus:border-cyber-cyan focus:ring-1 focus:ring-cyber-cyan/40 transition-all font-mono';

const SUBMIT_BTN_CLS =
  'flex-shrink-0 p-3 rounded-xl transition-all bg-cyber-emerald text-obsidian-900 hover:bg-emerald-300 shadow-[0_0_15px_rgba(0,255,136,0.4)] hover:scale-[1.02]';

const FILE_BTN_CLS =
  'flex items-center space-x-2 bg-obsidian-900 border border-cyan-500/30 rounded-md px-2.5 py-1 text-[11px] text-slate-200';

export const MissionIntake: React.FC<{
  onOpenMissionDashboard: (challenge: Challenge) => void;
  onRefreshBackendData?: () => Promise<void> | void;
  onClose?: () => void;
}> = ({
  onOpenMissionDashboard,
  onRefreshBackendData,
  onClose,
}) => {
  const [state, setState] = useState<MissionIntakeState>({
    target: '',
    targetType: 'unknown',
    objective: '',
    files: [],
    uploading: false,
    submitting: false,
    error: null,
  });

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [state.objective]);

  const setTarget = (value: string, type: ParsedTarget['type']) => {
    setState((prev) => ({
      ...prev,
      target: value,
      targetType: type,
    }));
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setState((prev) => {
      const newFiles: UploadedArtifact[] = [];
      for (let i = 0; i < files.length; i++) {
        newFiles.push({
          name: files[i].name,
          path: URL.createObjectURL(files[i]),
          size: files[i].size,
        });
      }
      return {
        ...prev,
        files: [...prev.files, ...newFiles],
        uploading: false,
      };
    });
    e.target.value = '';
  };

  const removeArtifact = (index: number) => {
    setState((prev) => {
      const newFiles = [...prev.files];
      newFiles.splice(index, 1);
      return { ...prev, files: newFiles };
    });
  };

  const handleSubmit = async () => {
    if (!state.target.trim() || !state.objective.trim() || state.submitting) return;

    setState((prev) => ({ ...prev, submitting: true, error: null }));

    const request: MissionIntakeRequest = {
      target_address: state.target.trim(),
      objective: state.objective.trim(),
      attached_file_paths: state.files.map((f) => f.path),
    };

    try {
      const resp: MissionIntakeResponse = await apiService.startMission(request);
      soundEngine.playSuccess();

      // Convert the backend mission-intake response into the standard
      // frontend Challenge shape used by the workspace/selection flow.
      const formattedChallenge: Challenge = {
        id: resp.challenge_id,
        name: resp.challenge_name,
        category: resp.category,
        difficulty: 'MEDIUM',
        target: resp.target,
        status: (resp.status as Challenge['status']) || 'RUNNING',
        progress: resp.progress,
        lastActivity: 'Just now',
        flagStatus: resp.flag_status || 'UNFOUND',
        flag: resp.final_flag,
        description: resp.objective,
      };

      // Keep the challenge list in sync with the backend before navigating.
      if (onRefreshBackendData) {
        await onRefreshBackendData();
      }

      setTimeout(() => {
        onOpenMissionDashboard(formattedChallenge);
        onClose?.();
      }, 800);
    } catch (e: any) {
      console.error('Mission start failed:', e);
      setState((prev) => ({ ...prev, error: e?.message || 'Failed to start mission.' }));
      soundEngine.playWarning();
    } finally {
      setState((prev) => ({ ...prev, submitting: false }));
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-6.5rem)] font-mono text-slate-100 select-text max-w-5xl mx-auto w-full">
      {/* Header bar matching Forge standards */}
      <div className="flex items-center justify-between p-4 glass-panel rounded-xl border border-cyan-500/20 shrink-0 mb-4 bg-obsidian-950/80 shadow-[0_0_20px_rgba(0,0,0,0.5)]">
        <div className="flex items-center space-x-3.5">
          <div className="w-10 h-10 rounded-lg bg-obsidian-900 border border-cyber-cyan/50 flex items-center justify-center shadow-[0_0_15px_rgba(0,240,255,0.25)]">
            <Bot className="w-5 h-5 text-cyber-cyan" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-sm font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
                FORGE MISSION INTAKE
              </h1>
              <span className="px-2 py-0.5 rounded text-[9px] font-bold bg-cyan-950/80 border border-cyber-cyan/40 text-cyber-cyan uppercase">
                Mission Control
              </span>
            </div>
            <p className="text-[11px] text-slate-400">
              Launch a new offensive mission — backend infers category, capabilities, and pipeline
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={() => {
              soundEngine.playClick();
              setState((prev) => ({ ...prev, target: '', targetType: 'unknown', objective: '', files: [], error: null }));
            }}
            title="Reset Mission"
            className="p-2 rounded-lg border border-slate-800 hover:border-cyber-cyan/40 bg-obsidian-900 text-slate-400 hover:text-cyber-cyan transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Mission Form */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-1 pb-4 cyber-scrollbar">
        {/* Connection Loader */}
        {state.submitting && (
          <div className="flex flex-col items-center justify-center p-12 glass-panel rounded-xl border border-cyan-500/20 text-slate-400 space-y-3">
            <Loader2 className="w-8 h-8 animate-spin text-cyber-cyan" />
            <span className="text-xs tracking-wider uppercase font-bold text-cyber-cyan">
              Initializing FORGE mission…....
            </span>
          </div>
        )}

        {/* Error Banner */}
        {state.error && (
          <div className="flex items-start space-x-3 bg-rose-950/40 border border-cyber-rose/60 rounded-xl p-3.5 text-xs text-rose-300 shadow-[0_0_15px_rgba(255,0,85,0.2)]">
            <AlertTriangle className="w-4 h-4 shrink-0 text-cyber-rose mt-0.5" />
            <div className="flex-1">
              <span className="font-bold uppercase tracking-wider block text-cyber-rose">Mission Error</span>
              <span>{state.error}</span>
            </div>
            <button
              onClick={() => setState((prev) => ({ ...prev, error: null }))}
              className="text-rose-400 hover:text-white"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Mission Form Card */}
        <div className="space-y-4">
          {/* TARGET field */}
          <div>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">TARGET</label>
            <span className="text-xs text-slate-500 mb-1.5 block">
              URL, IP, hostname, or `nc host port` format
            </span>
            <textarea
              value={state.target}
              onChange={(e) => {
                const type = parseTargetType(e.target.value);
                setTarget(e.target.value, type);
              }}
              onKeyDown={handleKeyDown}
              disabled={state.submitting}
              placeholder={state.targetType !== 'unknown' ? `Target (${state.targetType})...` : 'Enter target (IP/URL/host/nc host port)…'}
              rows={1}
              className={`${MISSION_INTAKE_CLS} resize-none min-h-[44px] max-h-32`}
              autoFocus
            />
            {state.targetType !== 'unknown' && (
              <div className="mt-2 flex items-center space-x-2 text-xs">
                <span className="px-2 py-0.5 rounded bg-cyan-950/80 border border-cyber-cyan/40 text-cyber-cyan text-[9px] font-bold uppercase">
                  Detected: {state.targetType}
                </span>
              </div>
            )}
          </div>

          {/* ARTIFACT field */}
          <div>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">ARTIFACT</label>
            <span className="text-xs text-slate-500 mb-1.5 block">
              Upload one or more challenge files (optional)
            </span>
            <div>
              <button
                type="button"
                onClick={() => {
                  soundEngine.playClick();
                  inputRef.current?.click();
                }}
                className="px-3 py-2 rounded-lg bg-obsidian-900 border border-slate-800 hover:border-cyber-cyan/40 text-slate-400 hover:text-cyber-cyan transition-colors font-semibold text-xs uppercase hover:scale-[1.02]"
                title="Attach challenge file"
              >
                <Paperclip className="w-3.5 h-3.5 text-cyber-cyan mr-1" />Attach Files
              </button>
              <input
                ref={inputRef}
                type="file"
                multiple
                className="hidden"
                onChange={handleFileSelect}
              />
            </div>
            {state.files.length > 0 && (
              <div className="mt-2 space-y-2 max-h-32 overflow-y-auto">
                {state.files.map((f, i) => (
                  <div key={i} className={`${FILE_BTN_CLS} flex items-center space-x-2`}>
                    <FileCode className="w-3 h-3 text-cyber-cyan shrink-0" />
                    <span className="font-semibold">{f.name}</span>
                    <span className="text-slate-500">({(f.size / 1024).toFixed(1)} KB)</span>
                    <button
                      type="button"
                      onClick={() => removeArtifact(i)}
                      className="text-slate-500 hover:text-cyber-rose transition-colors ml-1"
                      title="Remove attachment"
                    >
                      <X className="w-2.5 h-2.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* OBJECTIVE field */}
          <div>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">OBJECTIVE</label>
            <span className="text-xs text-slate-500 mb-1.5 block">
              What are you looking for?
            </span>
            <textarea
              value={state.objective}
              onChange={(e) => setState((prev) => ({ ...prev, objective: e.target.value }))}
              onKeyDown={handleKeyDown}
              disabled={state.submitting}
              placeholder="e.g. Find the flag • Find the vulnerability and retrieve the flag • Analyze this binary and recover the flag"
              rows={1}
              className={`${MISSION_INTAKE_CLS} resize-none min-h-[44px] max-h-32`}
            />
          </div>
        </div>

        {/* Action Bar */}
        <div className="shrink-0 pt-3 border-t border-cyan-500/20">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSubmit();
            }}
            className="glass-panel border border-slate-800/50 p-3 rounded-2xl flex items-end space-x-3 bg-obsidian-950/80 shadow-xl"
          >
            <div className="flex-1 relative">
              {/* Submit button */}
              <button
                type="submit"
                disabled={state.submitting || !state.target.trim() || !state.objective.trim()}
                className={`flex-shrink-0 p-3 rounded-xl transition-all ${state.submitting || !state.target.trim() || !state.objective.trim() ? 'bg-obsidian-800 text-slate-600 border border-slate-800 cursor-not-allowed' : SUBMIT_BTN_CLS}`}
                title="Start FORGE Mission"
              >
                <Send className="w-5 h-5" /> START FORGE
              </button>
            </div>
          </form>
        </div>

        <div ref={bottomRef} />
      </div>
    </div>
  );
};