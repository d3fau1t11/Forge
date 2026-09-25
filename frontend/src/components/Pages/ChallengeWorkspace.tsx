import React, { useState, useEffect, useRef } from 'react';
import { Wifi, WifiOff, RefreshCw } from 'lucide-react';
import { 
  Play, 
  Pause, 
  ChevronLeft, 
  Copy, 
  Check, 
  Sparkles,
  ArrowRight,
  Shield,
  Layers,
  Terminal,
  Cpu,
  FileText,
  AlertTriangle,
  FileCode,
  Folder,
  ListTodo,
  Clock,
  Zap,
  Save,
  MessageSquare,
  Send,
  Bot,
  User,
  Loader2,
  Sliders
} from 'lucide-react';
import { Challenge, Target, EvidenceItem, AiDecision, TerminalLog, Finding, WorkflowNode, AgentInfo, ChallengeChatMessage, ChallengeTab } from '../../types';
import { soundEngine } from '../../utils/soundEngine';
import { computeElapsedSeconds, formatDuration } from '../../utils/timeUtils';
import { apiService } from '../../services/api';

interface ChallengeWorkspaceProps {
  challenge: Challenge;
  target: Target;
  agents: AgentInfo[];
  evidenceList: EvidenceItem[];
  decisions: AiDecision[];
  logs: TerminalLog[];
  findings: Finding[];
  workflowNodes: WorkflowNode[];
  checkpoint?: { cycle: number; report: string };
  onSubmitCheckpoint?: (text: string) => Promise<any>;
  onBackToChallenges: () => void;
  onToggleStatus: (id: string) => void;
  wsStatus?: 'CONNECTING' | 'CONNECTED' | 'DISCONNECTED' | 'RECONNECTING' | 'ERROR';
  backendError?: string | null;
}

function renderMarkdownContent(text: string): React.ReactNode {
  if (!text) return null;
  const codeBlockRegex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
  const parts: Array<{ type: 'text' | 'codeblock'; lang?: string; content: string }> = [];
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: text.substring(lastIndex, match.index) });
    }
    parts.push({ type: 'codeblock', lang: match[1] || 'bash', content: match[2] });
    lastIndex = codeBlockRegex.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push({ type: 'text', content: text.substring(lastIndex) });
  }

  return parts.map((part, idx) => {
    if (part.type === 'codeblock') {
      return (
        <div key={idx} className="my-2.5 bg-obsidian-950 border border-cyber-cyan/30 rounded-lg overflow-hidden font-mono text-[11px]">
          {part.lang && (
            <div className="bg-obsidian-900 px-3 py-1 border-b border-slate-800 text-[10px] text-cyber-cyan font-bold uppercase tracking-wider flex justify-between items-center">
              <span>{part.lang}</span>
            </div>
          )}
          <pre className="p-3 text-slate-200 overflow-x-auto whitespace-pre-wrap select-all">{part.content}</pre>
        </div>
      );
    }

    const inlineParts = part.content.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    return (
      <span key={idx} className="leading-relaxed">
        {inlineParts.map((sub, sIdx) => {
          if (sub.startsWith('**') && sub.endsWith('**')) {
            return <strong key={sIdx} className="text-cyber-cyan font-bold">{sub.slice(2, -2)}</strong>;
          }
          if (sub.startsWith('`') && sub.endsWith('`')) {
            return (
              <code key={sIdx} className="bg-obsidian-900 border border-cyber-amber/30 text-amber-300 px-1.5 py-0.5 rounded text-[11px] font-mono">
                {sub.slice(1, -1)}
              </code>
            );
          }
          return sub.split('\n').map((line, lineIdx, arr) => (
            <React.Fragment key={`${sIdx}-${lineIdx}`}>
              {line}
              {lineIdx < arr.length - 1 && <br />}
            </React.Fragment>
          ));
        })}
      </span>
    );
  });
}

export const ChallengeWorkspace: React.FC<ChallengeWorkspaceProps> = ({
  challenge,
  target,
  agents,
  evidenceList,
  decisions,
  logs,
  findings,
  workflowNodes,
  checkpoint,
  onSubmitCheckpoint,
  onBackToChallenges,
  onToggleStatus,
  wsStatus = 'CONNECTED',
  backendError = null
}) => {
  const [activeTab, setActiveTab] = useState<ChallengeTab>('chat');

  // ── Persistent Forge Challenge Chat State ────────────────────────────────
  const [chatMessages, setChatMessages] = useState<ChallengeChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState<boolean>(false);
  const [chatSending, setChatSending] = useState<boolean>(false);
  const [chatInput, setChatInput] = useState<string>('');
  const [chatError, setChatError] = useState<string | null>(null);
  const [chatFilter, setChatFilter] = useState<'all' | 'chat_only' | 'events_only'>('all');
  const [currentMode, setCurrentMode] = useState<'auto' | 'manual'>(
    (challenge.approval_mode || challenge.approvalMode) === 'auto' ? 'auto' : 'manual'
  );
  const [modeUpdating, setModeUpdating] = useState<boolean>(false);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  // Sync mode whenever challenge prop updates from parent or WS
  useEffect(() => {
    const m = challenge.approval_mode || challenge.approvalMode;
    if (m === 'auto' || m === 'manual') {
      setCurrentMode(m);
    }
  }, [challenge.approval_mode, challenge.approvalMode]);

  // Load persistent chat history on challenge switch or mount
  useEffect(() => {
    let isMounted = true;
    const loadMessages = async () => {
      if (!challenge.id) return;
      setChatLoading(true);
      setChatError(null);
      try {
        const history = await apiService.getChallengeMessages(challenge.id);
        if (isMounted) {
          setChatMessages(history);
        }
      } catch (e: any) {
        if (isMounted) {
          setChatError(e?.message || 'Failed to fetch challenge chat history');
        }
      } finally {
        if (isMounted) setChatLoading(false);
      }
    };
    loadMessages();
    return () => { isMounted = false; };
  }, [challenge.id]);

  // Auto-scroll chat on message / live activity updates
  useEffect(() => {
    if (activeTab === 'chat') {
      chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, chatSending, activeTab, logs.length, evidenceList.length, findings.length]);

  const handleToggleMode = async (newMode: 'auto' | 'manual') => {
    if (currentMode === newMode || modeUpdating) return;
    soundEngine.playClick();
    setModeUpdating(true);
    try {
      await apiService.updateChallengeMode(challenge.id, newMode);
      setCurrentMode(newMode);
      soundEngine.playSuccess();
    } catch (e: any) {
      console.error('Failed to update challenge mode:', e);
    } finally {
      setModeUpdating(false);
    }
  };

  const handleSendMessage = async (customPrompt?: string) => {
    const content = (customPrompt !== undefined ? customPrompt : chatInput).trim();
    if (!content || chatSending) return;

    soundEngine.playClick();
    const tempUserMsg: ChallengeChatMessage = {
      id: `temp-${Date.now()}`,
      challenge_id: challenge.id,
      role: 'user',
      content,
      created_at: new Date().toISOString()
    };

    setChatMessages((prev) => [...prev, tempUserMsg]);
    if (customPrompt === undefined) setChatInput('');
    setChatSending(true);
    setChatError(null);

    try {
      const res = await apiService.postChallengeMessage(challenge.id, content);
      soundEngine.playSuccess();
      setChatMessages((prev) => {
        const filtered = prev.filter((m) => m.id !== tempUserMsg.id);
        return [...filtered, res.user_message, res.assistant_message];
      });
    } catch (e: any) {
      soundEngine.playWarning();
      setChatError(e?.message || 'Failed to receive response from Forge Assistant');
    } finally {
      setChatSending(false);
    }
  };

  const startedStr = challenge.startedAt || challenge.started_at || challenge.createdAt || challenge.created_at;
  const completedStr = challenge.completedAt || challenge.completed_at;
  const initialDuration = computeElapsedSeconds(startedStr, completedStr, challenge.durationSeconds || challenge.duration_seconds, challenge.status);

  const [elapsedSeconds, setElapsedSeconds] = useState<number>(initialDuration);

  useEffect(() => {
    let interval: any = null;

    const tick = () => {
      const currentStarted = challenge.startedAt || challenge.started_at || challenge.createdAt || challenge.created_at;
      const currentCompleted = challenge.completedAt || challenge.completed_at;
      const updated = computeElapsedSeconds(currentStarted, currentCompleted, challenge.durationSeconds || challenge.duration_seconds, challenge.status);
      setElapsedSeconds(updated);
    };

    tick();
    if (challenge.status === 'RUNNING') {
      interval = setInterval(tick, 1000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [challenge.id, challenge.status, challenge.startedAt, challenge.started_at, challenge.createdAt, challenge.created_at, challenge.completedAt, challenge.completed_at, challenge.durationSeconds, challenge.duration_seconds]);

  const activeWorkflowNodes: WorkflowNode[] = workflowNodes.length > 0 ? workflowNodes : [
    { id: 'wn-1', label: '1. INGEST', status: 'COMPLETED', description: `Challenge scope & target ${challenge.target} initialized in workspace.` },
    { id: 'wn-2', label: '2. RECON', status: challenge.progress >= 20 ? 'COMPLETED' : (challenge.status === 'RUNNING' ? 'ACTIVE' : 'PENDING'), description: 'Target IP scanning & service version fingerprinting.' },
    { id: 'wn-3', label: '3. ENUM', status: challenge.progress >= 40 ? 'COMPLETED' : (challenge.progress >= 20 && challenge.status === 'RUNNING' ? 'ACTIVE' : 'PENDING'), description: 'Directory enumeration & web surface discovery.' },
    { id: 'wn-4', label: '4. ANALYSIS', status: challenge.progress >= 60 ? 'COMPLETED' : (challenge.progress >= 40 && challenge.status === 'RUNNING' ? 'ACTIVE' : 'PENDING'), description: 'AI vulnerability assessment & exploit path strategy formulation.' },
    { id: 'wn-5', label: '5. EXPLOITATION', status: challenge.progress >= 80 ? 'COMPLETED' : (challenge.progress >= 60 && challenge.status === 'RUNNING' ? 'ACTIVE' : 'PENDING'), description: 'Exploit payload execution & privilege verification.' },
    { id: 'wn-6', label: '6. FLAG CAPTURE', status: challenge.flagStatus === 'CAPTURED' ? 'COMPLETED' : (challenge.progress >= 80 && challenge.status === 'RUNNING' ? 'ACTIVE' : 'PENDING'), description: 'Flag extraction from file system, memory, or database.' }
  ];

  const [selectedWorkflowNode, setSelectedWorkflowNode] = useState<WorkflowNode | null>(activeWorkflowNodes[0]);
  const [copiedReadme, setCopiedReadme] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // ── HITL checkpoint (hard pause & wait) local UI state ──────────────────────
  const [checkpointPaste, setCheckpointPaste] = useState('');
  const [checkpointBusy, setCheckpointBusy] = useState(false);
  const [checkpointResult, setCheckpointResult] = useState<string | null>(null);
  const [checkpointCopied, setCheckpointCopied] = useState(false);
  const [flagCopied, setFlagCopied] = useState(false);

  const handleCopyFlag = () => {
    if (!challenge.flag) return;
    try {
      navigator.clipboard.writeText(challenge.flag);
      setFlagCopied(true);
      setTimeout(() => setFlagCopied(false), 1500);
    } catch (e) { /* clipboard blocked — the field is select-all as a fallback */ }
  };

  const handleCopyCheckpoint = () => {
    if (!checkpoint) return;
    try {
      navigator.clipboard.writeText(checkpoint.report);
      setCheckpointCopied(true);
      setTimeout(() => setCheckpointCopied(false), 1500);
    } catch (e) { /* clipboard blocked — user can select manually */ }
  };

  const handleSubmitCheckpoint = async () => {
    if (!onSubmitCheckpoint || !checkpointPaste.trim()) return;
    setCheckpointBusy(true);
    setCheckpointResult(null);
    try {
      const res = await onSubmitCheckpoint(checkpointPaste);
      if (res && res.accepted === false) {
        setCheckpointResult(`Not accepted: ${res.reason || 'no active checkpoint'}`);
      } else if (res && res.parsed) {
        setCheckpointResult(`Routed directives to: ${(res.routed || []).join(', ') || '(none)'}${res.unknown_labels && res.unknown_labels.length ? ` | unknown labels: ${res.unknown_labels.join(', ')}` : ''}. Resuming.`);
        setCheckpointPaste('');
      } else {
        setCheckpointResult('No suggestion delimiters found — applied the paste as general guidance to all agents. Resuming.');
        setCheckpointPaste('');
      }
      try { soundEngine.playSuccess(); } catch (e) {}
    } catch (e: any) {
      setCheckpointResult(`Failed to submit: ${e?.message || e}`);
    } finally {
      setCheckpointBusy(false);
    }
  };

  // Writeup content state — the writeup is authored on the BACKEND (AI, Gemini-first)
  // from the real run telemetry; the frontend only previews and (on Save) persists it
  // into the challenge working folder.
  const [writeupText, setWriteupText] = useState<string>('');
  const [writeupBusy, setWriteupBusy] = useState<boolean>(false);
  const [writeupBy, setWriteupBy] = useState<string>('');
  const [writeupSaveMsg, setWriteupSaveMsg] = useState<string>('');
  // True once we are showing a writeup that has already been SAVED (Task #2): the
  // tab shows the saved artifact instead of re-authoring a fresh one on every open.
  const [writeupSaved, setWriteupSaved] = useState<boolean>(false);

  const handleTabChange = (tabKey: any) => {
    soundEngine.playClick();
    setActiveTab(tabKey);
  };

  const handleCopyWriteup = () => {
    soundEngine.playSuccess();
    navigator.clipboard.writeText(writeupText);
    setCopiedReadme(true);
    setTimeout(() => setCopiedReadme(false), 2000);
  };

  const handleCopyText = (id: string, text: string) => {
    soundEngine.playClick();
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // force=false: prefer the already-saved writeup (default, on tab open).
  // force=true: author a FRESH draft, ignoring any saved one (AUTO WRITEUP button).
  const handleGenerateWriteup = async (force: boolean = false) => {
    soundEngine.playClick();
    setWriteupBusy(true);
    setWriteupSaveMsg('');
    try {
      const data = await apiService.getWriteup(challenge.id, force);
      setWriteupText(data.content || '# No run telemetry yet\n\nRun this challenge first, then generate the writeup.');
      setWriteupBy(data.generated_by || '');
      setWriteupSaved(Boolean(data.saved));
      soundEngine.playSuccess();
    } catch (e) {
      // keep any existing text on error
    } finally {
      setWriteupBusy(false);
    }
  };

  const handleSaveWriteup = async () => {
    soundEngine.playClick();
    try {
      const res = await apiService.saveWriteup(challenge.id, writeupText);
      setWriteupSaveMsg(`SAVED → ${res.file_path}`);
      setWriteupSaved(true);
      setWriteupBy('saved');
      soundEngine.playSuccess();
      setTimeout(() => setWriteupSaveMsg(''), 8000);
    } catch (e: any) {
      setWriteupSaveMsg(`Save failed: ${e?.message || e}`);
      setTimeout(() => setWriteupSaveMsg(''), 8000);
    }
  };

  // On first open of the WRITEUP MD tab, prefer the ALREADY-SAVED writeup; only
  // author a fresh one when none has been saved yet (handled server-side).
  useEffect(() => {
    if (activeTab === 'readme' && !writeupText && !writeupBusy) {
      handleGenerateWriteup(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const currentPlan = challenge.missionPlan || challenge.mission_plan;

  // Live swarm fleet — real worker telemetry from /api/agents + AGENT_UPDATE events.
  // Falls back to the three role placeholders (STANDBY) when nothing is live.
  const WORKER_ROLES: { name: string; icon: string; model: string; blurb: string }[] = [
    { name: 'RECON', icon: '🛰️', model: 'Groq Qwen / xKiro (Zero Cost)', blurb: 'Port scanning, directory fuzzing & endpoint harvesting' },
    { name: 'CRYPTO', icon: '🔬', model: 'Mistral Codestral / xKiro', blurb: 'Decompiling scripts, ROT13/JWT token & comment decoding' },
    { name: 'PWN', icon: '⚡', model: 'xKiro Qwen Coder / DeepSeek', blurb: 'Crafting auth bypass headers, SQLi & payload delivery' }
  ];
  const fleetStatusClass = (status: string) => {
    switch (status) {
      case 'RUNNING': return 'text-cyber-emerald animate-pulse';
      case 'ANALYZING': return 'text-cyber-cyan animate-pulse';
      case 'FAILED': return 'text-cyber-rose';
      default: return 'text-slate-500';
    }
  };
  const hasLiveFleet = agents.length > 0;

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-10">
      {/* Connectivity / Backend Error Banner — visible when WS or API is unavailable */}
      {(wsStatus !== 'CONNECTED' || backendError) && (
        <div className={`rounded-xl p-3 border flex items-center space-x-3 text-xs font-bold ${
          backendError
            ? 'bg-rose-950/70 border-rose-500/60 text-rose-300 shadow-[0_0_15px_rgba(244,63,94,0.2)]'
            : 'bg-amber-950/70 border-amber-500/60 text-amber-300 shadow-[0_0_15px_rgba(245,158,11,0.2)]'
        }`}>
          {wsStatus === 'CONNECTED' ? (
            <Wifi className="w-4 h-4 text-emerald-400 shrink-0" />
          ) : wsStatus === 'RECONNECTING' || wsStatus === 'CONNECTING' ? (
            <RefreshCw className="w-4 h-4 animate-spin shrink-0" />
          ) : (
            <WifiOff className="w-4 h-4 shrink-0" />
          )}
          <div className="flex flex-col">
            {wsStatus !== 'CONNECTED' && (
              <span>WebSocket: {wsStatus} — live events may be delayed. Polling backend every 10s.</span>
            )}
            {backendError && (
              <span>⚠️ API Error: {backendError} — showing cached state.</span>
            )}
          </div>
        </div>
      )}

      {/* Captured flag — prominent, one-click copy (click the field to select-all as a fallback) */}
      {challenge.flag && (
        <div className="glass-panel border-2 border-cyber-emerald/60 rounded-xl p-5 space-y-3 shadow-[0_0_30px_rgba(16,185,129,0.2)] cyber-corner">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-display font-bold tracking-wider text-cyber-emerald uppercase flex items-center space-x-2">
              <Check className="w-4 h-4" />
              <span>Flag Captured</span>
            </h3>
            <button
              onClick={() => { soundEngine.playClick(); handleCopyFlag(); }}
              className="px-3 py-1.5 rounded-lg bg-obsidian-900 border border-cyber-emerald/50 text-cyber-emerald text-xs font-bold flex items-center space-x-1.5 hover:bg-emerald-950/50 transition-all"
            >
              {flagCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{flagCopied ? 'COPIED' : 'COPY FLAG'}</span>
            </button>
          </div>
          <input
            readOnly
            value={challenge.flag}
            onFocus={(e) => e.currentTarget.select()}
            onClick={(e) => e.currentTarget.select()}
            aria-label="Captured flag"
            className="w-full bg-obsidian-950 border border-cyber-emerald/40 rounded-lg px-3 py-3 text-sm md:text-base text-cyber-emerald font-mono tracking-wide select-all focus:outline-none focus:border-cyber-emerald"
          />
          <p className="text-[11px] text-slate-500">Click the field to select it all, or use COPY FLAG. Verified from real tool output.</p>
        </div>
      )}

      {/* HITL Checkpoint — hard pause & wait (manual copy-paste to a stronger model) */}
      {checkpoint && (
        <div className="glass-panel border-2 border-cyber-amber/60 rounded-xl p-5 space-y-3 shadow-[0_0_30px_rgba(245,158,11,0.18)] cyber-corner">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-display font-bold tracking-wider text-cyber-amber uppercase flex items-center space-x-2">
              <AlertTriangle className="w-4 h-4" />
              <span>Operator Checkpoint — Cycle {checkpoint.cycle} (run paused)</span>
            </h3>
            <button
              onClick={handleCopyCheckpoint}
              className="px-3 py-1.5 rounded-lg bg-obsidian-900 border border-cyber-amber/50 text-cyber-amber text-xs font-bold flex items-center space-x-1.5 hover:bg-amber-950/50 transition-all"
            >
              {checkpointCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{checkpointCopied ? 'COPIED' : 'COPY REPORT'}</span>
            </button>
          </div>
          <p className="text-[11px] text-slate-400 leading-relaxed">
            All agents are halted. Copy this consolidated report into a stronger external model, then paste its
            response below. Route directives with <span className="text-cyber-cyan font-bold">--- suggestion: {'{agent_id}'} ---</span> markers.
          </p>
          <textarea
            readOnly
            value={checkpoint.report}
            rows={12}
            className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-3 text-[11px] text-slate-200 font-mono leading-relaxed custom-scrollbar select-all"
          />
          <div className="space-y-2">
            <label className="block text-[11px] text-slate-400 uppercase font-bold">Paste external model response</label>
            <textarea
              value={checkpointPaste}
              onChange={(e) => setCheckpointPaste(e.target.value)}
              rows={6}
              placeholder={"--- suggestion: agent_1 ---\n<directive text>\n\n--- suggestion: agent_2 ---\n<directive text>"}
              className="w-full bg-obsidian-950 border border-cyber-cyan/40 rounded-lg p-3 text-[11px] text-slate-100 font-mono leading-relaxed focus:outline-none focus:border-cyber-cyan custom-scrollbar"
            />
          </div>
          {checkpointResult && (
            <div className="text-[11px] text-cyber-emerald bg-emerald-950/40 border border-emerald-800/60 rounded-lg p-2.5">{checkpointResult}</div>
          )}
          <div className="flex justify-end">
            <button
              onClick={handleSubmitCheckpoint}
              disabled={checkpointBusy || !checkpointPaste.trim()}
              className="px-5 py-2.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 disabled:opacity-40 disabled:cursor-not-allowed text-obsidian-950 font-display font-bold text-xs uppercase tracking-wider flex items-center space-x-2 shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all"
            >
              <span>{checkpointBusy ? 'APPLYING…' : 'APPLY & RESUME'}</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Workspace Top Header Bar */}
      <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-5 space-y-4 shadow-[0_0_30px_rgba(0,240,255,0.15)] cyber-corner">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
          <div className="flex items-center space-x-4">
            <button
              onClick={() => { soundEngine.playClick(); onBackToChallenges(); }}
              className="p-2 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-300 transition-all hover:scale-105"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>

            <div>
              <div className="flex items-center space-x-3 flex-wrap gap-y-1">
                <h1 className="text-2xl font-display font-bold tracking-wider text-slate-100 neon-text-cyan">{challenge.name}</h1>
                <span className="px-2.5 py-1 rounded bg-cyan-950/80 border border-cyber-cyan/60 text-cyber-cyan text-xs font-bold uppercase">
                  {challenge.category} CTF
                </span>
                <span className="px-2.5 py-0.5 rounded bg-obsidian-900 border border-slate-700 text-slate-300 text-[11px] font-bold font-mono">
                  RUN ID: {challenge.id}
                </span>
                {challenge.status === 'RUNNING' && (
                  <span className="px-2.5 py-0.5 rounded bg-emerald-950/90 border border-emerald-500/80 text-emerald-300 text-[11px] font-bold font-mono flex items-center space-x-1 animate-pulse shadow-[0_0_10px_rgba(16,185,129,0.3)]">
                    <Zap className="w-3 h-3 text-emerald-400 fill-emerald-400" />
                    <span>⚡ SWARM ACTIVE & RUNNING</span>
                  </span>
                )}
                {challenge.status === 'AWAITING_FLAG' && (
                  <span className="px-2.5 py-0.5 rounded bg-purple-950/90 border border-purple-500/80 text-purple-300 text-[11px] font-bold font-mono animate-pulse">
                    🚩 AWAITING FLAG VERIFICATION
                  </span>
                )}
                {challenge.status === 'FAILED' && (
                  <span className="px-2.5 py-0.5 rounded bg-rose-950/90 border border-rose-500/80 text-rose-300 text-[11px] font-bold font-mono">
                    ❌ RUN FAILED / STALLED
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Action Controls & Uptime & Progress */}
          <div className="flex items-center space-x-4 flex-wrap gap-2">
            <div className="flex items-center space-x-2 text-xs bg-obsidian-900/90 px-3 py-1.5 rounded-lg border border-slate-800 shadow-inner">
              <Clock className="w-3.5 h-3.5 text-cyber-cyan" />
              <span className="text-slate-400 font-bold text-[10px] uppercase">LAST EVENT:</span>
              <span className="text-slate-100 font-bold font-mono text-xs">{challenge.lastActivity || 'Just now'}</span>
            </div>

            <div className="flex items-center space-x-2 text-xs bg-obsidian-900/90 px-3 py-1.5 rounded-lg border border-slate-800 shadow-inner">
              <Clock className="w-3.5 h-3.5 text-cyber-cyan" />
              <span className="text-slate-400 font-bold text-[10px] uppercase">UPTIME:</span>
              <span className="text-slate-100 font-bold font-mono text-xs">{formatDuration(elapsedSeconds)}</span>
            </div>

            <div className="flex items-center space-x-2 text-xs">
              <span className={`w-3 h-3 rounded-full ${
                challenge.status === 'RUNNING' ? 'bg-cyber-emerald animate-ping' :
                challenge.status === 'FAILED' ? 'bg-rose-500' :
                challenge.status === 'COMPLETED' ? 'bg-emerald-400' : 'bg-cyber-amber'
              }`}></span>
              <span className={`font-bold tracking-wider ${
                challenge.status === 'RUNNING' ? 'text-cyber-emerald' :
                challenge.status === 'FAILED' ? 'text-rose-400' :
                challenge.status === 'COMPLETED' ? 'text-emerald-400' : 'text-amber-400'
              }`}>● {challenge.status}</span>
            </div>

            <button
              onClick={() => { soundEngine.playClick(); onToggleStatus(challenge.id); }}
              className={`px-4 py-2 rounded-lg text-xs font-bold flex items-center space-x-2 border transition-all ${
                challenge.status === 'RUNNING'
                  ? 'bg-amber-950/80 border-amber-700 text-cyber-amber hover:bg-amber-900'
                  : 'bg-emerald-950/80 border-emerald-700 text-cyber-emerald hover:bg-emerald-900'
              }`}
            >
              {challenge.status === 'RUNNING' ? (
                <>
                  <Pause className="w-4 h-4" />
                  <span>PAUSE OPERATION</span>
                </>
              ) : (
                <>
                  <Play className="w-4 h-4" />
                  <span>RESUME RUN</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Dynamic Progress Bar Row */}
        <div className="flex items-center space-x-4 text-xs">
          <span className="text-slate-400 font-bold shrink-0">MISSION COMPLETION:</span>
          <div className="flex-1 h-2.5 bg-obsidian-950 rounded-full overflow-hidden border border-slate-800">
            <div
              className="h-full bg-cyber-cyan transition-all duration-500 shadow-[0_0_12px_#00f0ff]"
              style={{ width: `${challenge.progress}%` }}
            ></div>
          </div>
          <span className="text-cyber-cyan font-bold text-sm shrink-0">{challenge.progress}%</span>
        </div>
      </div>

      {/* 9 Workspace Navigation Tabs */}
      <div className="flex items-center space-x-2 border-b border-slate-800 pb-2 overflow-x-auto text-xs font-mono">
        {[
          { key: 'chat', label: 'FORGE AI CHAT', icon: MessageSquare },
          { key: 'overview', label: 'OVERVIEW', icon: Shield },
          { key: 'todo_plan', label: 'MISSION TODO LIST', icon: ListTodo },
          { key: 'workflow', label: 'PIPELINE GRAPH', icon: Layers },
          { key: 'terminal', label: 'TERMINAL', icon: Terminal },
          { key: 'ai_decisions', label: 'AI REASONING', icon: Cpu },
          { key: 'evidence', label: 'EVIDENCE VAULT', icon: FileText },
          { key: 'findings', label: 'FINDINGS', icon: AlertTriangle },
          { key: 'readme', label: 'WRITEUP MD', icon: FileCode }
        ].map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => handleTabChange(t.key)}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center space-x-2 whitespace-nowrap ${
                activeTab === t.key
                  ? 'bg-cyber-cyan text-obsidian-950 font-display shadow-[0_0_15px_rgba(0,240,255,0.4)]'
                  : 'bg-obsidian-900/60 text-slate-400 hover:text-slate-200 border border-slate-800'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{t.label}</span>
              {t.key === 'chat' && chatMessages.length > 0 && (
                <span className="ml-1 px-1.5 py-0.2 rounded-full bg-obsidian-950 text-[10px] text-cyber-cyan font-bold border border-cyber-cyan/40">
                  {chatMessages.length}
                </span>
              )}
              {t.key === 'todo_plan' && currentPlan?.tasks && (
                <span className="ml-1 px-1.5 py-0.2 rounded-full bg-obsidian-950 text-[10px] text-cyber-cyan font-bold border border-cyber-cyan/40">
                  {currentPlan.tasks.filter(tk => tk.status === 'COMPLETED').length}/{currentPlan.tasks.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* TAB 0: FORGE AI CHAT (PERSISTENT & LIVE STATE-AWARE) */}
      {activeTab === 'chat' && (
        <div className="space-y-4 font-mono text-xs">
          {/* Chat Pane Header & Controls */}
          <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-4 space-y-3 shadow-[0_0_25px_rgba(0,240,255,0.15)] cyber-corner">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
              <div className="flex items-center space-x-3">
                <div className="w-9 h-9 rounded-lg bg-cyan-950/80 border border-cyber-cyan flex items-center justify-center text-cyber-cyan shadow-[0_0_12px_rgba(0,240,255,0.3)]">
                  <Bot className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center space-x-2">
                    <h2 className="font-display font-bold text-slate-100 uppercase tracking-wider text-sm neon-text-cyan">
                      FORGE AI ASSISTANT
                    </h2>
                    <span className="px-2 py-0.5 rounded bg-cyan-950/80 border border-cyber-cyan/50 text-cyber-cyan text-[10px] font-bold">
                      LIVE STATE-AWARE
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-400">
                    Conversing about <span className="text-cyber-cyan font-bold">{challenge.name}</span> with access to real telemetry, tools, and evidence.
                  </p>
                </div>
              </div>

              {/* Per-Challenge Execution Mode & Filter Controls */}
              <div className="flex items-center space-x-3 flex-wrap gap-y-2">
                {/* Auto / Manual Mode Toggle */}
                <div className="flex items-center space-x-2 bg-obsidian-950 px-3 py-1.5 rounded-lg border border-slate-800">
                  <Sliders className="w-3.5 h-3.5 text-cyber-cyan" />
                  <span className="text-[10px] text-slate-400 font-bold uppercase">MODE:</span>
                  <div className="flex items-center space-x-1">
                    <button
                      onClick={() => handleToggleMode('auto')}
                      disabled={modeUpdating}
                      className={`px-2 py-0.5 rounded text-[10px] font-bold transition-all flex items-center space-x-1 ${
                        currentMode === 'auto'
                          ? 'bg-cyber-emerald/20 border border-cyber-emerald text-cyber-emerald shadow-[0_0_8px_rgba(0,255,136,0.3)]'
                          : 'bg-obsidian-900 border border-slate-800 text-slate-400 hover:text-slate-200'
                      }`}
                      title="Autonomous execution — agents proceed without manual pauses"
                    >
                      <Zap className="w-3 h-3" />
                      <span>AUTO</span>
                    </button>
                    <button
                      onClick={() => handleToggleMode('manual')}
                      disabled={modeUpdating}
                      className={`px-2 py-0.5 rounded text-[10px] font-bold transition-all flex items-center space-x-1 ${
                        currentMode === 'manual'
                          ? 'bg-cyber-amber/20 border border-cyber-amber text-cyber-amber shadow-[0_0_8px_rgba(245,158,11,0.3)]'
                          : 'bg-obsidian-900 border border-slate-800 text-slate-400 hover:text-slate-200'
                      }`}
                      title="Manual approval mode — operator checkpoint review enabled"
                    >
                      <Shield className="w-3 h-3" />
                      <span>MANUAL</span>
                    </button>
                  </div>
                </div>

                {/* Stream Filter Switcher */}
                <div className="flex items-center space-x-1 bg-obsidian-950 p-1 rounded-lg border border-slate-800 text-[10px]">
                  <button
                    onClick={() => { soundEngine.playClick(); setChatFilter('all'); }}
                    className={`px-2 py-0.5 rounded font-bold transition-all ${
                      chatFilter === 'all'
                        ? 'bg-cyber-cyan/20 border border-cyber-cyan text-cyber-cyan'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    ALL ACTIVITY
                  </button>
                  <button
                    onClick={() => { soundEngine.playClick(); setChatFilter('chat_only'); }}
                    className={`px-2 py-0.5 rounded font-bold transition-all ${
                      chatFilter === 'chat_only'
                        ? 'bg-cyber-cyan/20 border border-cyber-cyan text-cyber-cyan'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    CHAT ONLY
                  </button>
                  <button
                    onClick={() => { soundEngine.playClick(); setChatFilter('events_only'); }}
                    className={`px-2 py-0.5 rounded font-bold transition-all ${
                      chatFilter === 'events_only'
                        ? 'bg-cyber-cyan/20 border border-cyber-cyan text-cyber-cyan'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    LIVE TELEMETRY
                  </button>
                </div>
              </div>
            </div>

            {/* Sub-header Live Feed Metric Strip */}
            <div className="flex items-center justify-between text-[11px] text-slate-400 px-1 pt-1 flex-wrap gap-2">
              <div className="flex items-center space-x-4">
                <span>Target: <code className="text-cyber-cyan font-bold">{target.currentIp}</code></span>
                <span>Category: <span className="text-purple-300 font-bold">{challenge.category}</span></span>
                <span>Run Status: <span className={challenge.status === 'RUNNING' ? 'text-cyber-emerald font-bold animate-pulse' : 'text-slate-300 font-bold'}>{challenge.status}</span></span>
              </div>
              <div className="flex items-center space-x-3 text-[10px]">
                <span className="flex items-center space-x-1">
                  <Terminal className="w-3 h-3 text-cyber-cyan" />
                  <span>{logs.length} cmds</span>
                </span>
                <span className="flex items-center space-x-1">
                  <FileText className="w-3 h-3 text-purple-400" />
                  <span>{evidenceList.length} evidence</span>
                </span>
                {challenge.flag && (
                  <span className="text-cyber-emerald font-bold flex items-center space-x-1 animate-pulse">
                    <Check className="w-3 h-3 text-cyber-emerald" />
                    <span>FLAG ACTIVE</span>
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Chat Messages Timeline Container */}
          <div className="glass-panel border border-slate-800 rounded-xl p-4 space-y-4 max-h-[580px] min-h-[440px] overflow-y-auto custom-scrollbar bg-obsidian-950/80 shadow-inner">
            {chatLoading && chatMessages.length === 0 && (
              <div className="flex items-center justify-center space-x-2 py-12 text-slate-400">
                <Loader2 className="w-5 h-5 text-cyber-cyan animate-spin" />
                <span>Loading persistent conversation history…</span>
              </div>
            )}

            {chatError && (
              <div className="p-3 bg-rose-950/60 border border-rose-500/60 text-rose-300 rounded-lg text-xs flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
                  <span>{chatError}</span>
                </div>
                <button
                  onClick={() => setChatError(null)}
                  className="text-slate-400 hover:text-slate-200 text-xs font-bold"
                >
                  ✕
                </button>
              </div>
            )}

            {/* Empty state welcome card */}
            {!chatLoading && chatMessages.length === 0 && (
              <div className="p-6 bg-obsidian-900/60 border border-slate-800 rounded-xl text-center space-y-2">
                <Sparkles className="w-6 h-6 text-cyber-cyan mx-auto animate-pulse" />
                <h3 className="font-display font-bold text-slate-200 text-sm">FORGE OPERATIONAL ASSISTANT INITIALIZED</h3>
                <p className="text-slate-400 text-xs max-w-lg mx-auto leading-relaxed">
                  Ask questions about the challenge objective, investigate reconnaissance telemetry, inspect discovered vulnerabilities, or coordinate exploitation strategies.
                </p>
              </div>
            )}

            {/* Conversation Messages */}
            {(chatFilter === 'all' || chatFilter === 'chat_only') && chatMessages.map((msg, idx) => {
              const isUser = msg.role === 'user';
              return (
                <div
                  key={msg.id || idx}
                  className={`flex items-start space-x-3 ${isUser ? 'flex-row-reverse space-x-reverse' : 'flex-row'}`}
                >
                  {/* Avatar Icon */}
                  <div
                    className={`w-8 h-8 rounded-lg shrink-0 flex items-center justify-center text-xs font-bold ${
                      isUser
                        ? 'bg-cyan-950 border border-cyber-cyan text-cyber-cyan shadow-[0_0_10px_rgba(0,240,255,0.3)]'
                        : 'bg-purple-950 border border-purple-500 text-purple-300 shadow-[0_0_10px_rgba(168,85,247,0.3)]'
                    }`}
                  >
                    {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
                  </div>

                  {/* Message Card */}
                  <div
                    className={`max-w-3xl rounded-xl p-3.5 space-y-1.5 shadow-md border ${
                      isUser
                        ? 'bg-obsidian-900/90 border-cyan-500/40 text-slate-100 rounded-tr-none'
                        : 'bg-obsidian-950/90 border-purple-500/30 text-slate-200 rounded-tl-none'
                    }`}
                  >
                    <div className="flex items-center justify-between text-[10px] border-b border-slate-800/80 pb-1 mb-1 gap-4">
                      <span className={`font-bold uppercase tracking-wider ${isUser ? 'text-cyber-cyan' : 'text-purple-300'}`}>
                        {isUser ? 'Operator' : 'Forge Assistant'}
                      </span>
                      {msg.created_at && (
                        <span className="text-slate-500 text-[9.5px]">
                          {new Date(msg.created_at).toLocaleTimeString()}
                        </span>
                      )}
                    </div>
                    <div className="text-xs leading-relaxed font-mono select-text">
                      {isUser ? (
                        <div className="whitespace-pre-wrap">{msg.content}</div>
                      ) : (
                        renderMarkdownContent(msg.content)
                      )}
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Live Telemetry Events (Tool executions, evidence, flags) */}
            {(chatFilter === 'all' || chatFilter === 'events_only') && (
              <div className="space-y-2 pt-2 border-t border-slate-800/60">
                <div className="flex items-center space-x-2 text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">
                  <Zap className="w-3 h-3 text-cyber-cyan" />
                  <span>Live Execution Stream (WebSocket)</span>
                </div>

                {/* Flag Captured Live Banner */}
                {challenge.flag && (
                  <div className="p-3 rounded-lg bg-emerald-950/50 border-2 border-cyber-emerald flex items-center justify-between shadow-[0_0_15px_rgba(0,255,136,0.2)]">
                    <div className="flex items-center space-x-2">
                      <Check className="w-4 h-4 text-cyber-emerald" />
                      <div>
                        <span className="font-bold text-cyber-emerald text-xs">FLAG CAPTURED & VERIFIED</span>
                        <p className="text-[11px] text-slate-200 font-mono select-all mt-0.5">{challenge.flag}</p>
                      </div>
                    </div>
                    <button
                      onClick={handleCopyFlag}
                      className="px-2.5 py-1 rounded bg-obsidian-950 border border-cyber-emerald text-cyber-emerald text-[10px] font-bold hover:bg-emerald-950"
                    >
                      {flagCopied ? 'COPIED' : 'COPY'}
                    </button>
                  </div>
                )}

                {/* Recent Tool Executions */}
                {logs.slice(0, 4).map((l) => (
                  <div
                    key={l.id}
                    className="p-2.5 rounded-lg bg-obsidian-900/80 border border-slate-800/80 flex flex-col space-y-1 text-[11px] hover:border-cyber-cyan/40 transition-colors"
                  >
                    <div className="flex justify-between items-center text-[10px] text-slate-400">
                      <span className="text-cyber-cyan font-bold flex items-center space-x-1">
                        <Terminal className="w-3 h-3 text-cyber-cyan" />
                        <span>$ {l.command}</span>
                      </span>
                      <span className={`px-1.5 py-0.2 rounded font-bold text-[9px] ${
                        l.exitCode === 0 ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800'
                      }`}>
                        EXIT {l.exitCode ?? 0}
                      </span>
                    </div>
                    {l.output && (
                      <pre className="text-[10px] text-slate-300 bg-obsidian-950 p-1.5 rounded border border-slate-900 overflow-x-auto max-h-16 whitespace-pre-wrap select-all">
                        {l.output.slice(0, 240)}
                      </pre>
                    )}
                  </div>
                ))}

                {/* Recent Evidence Vault Additions */}
                {evidenceList.slice(0, 2).map((ev) => (
                  <div
                    key={ev.id}
                    className="p-2 rounded-lg bg-obsidian-900/60 border border-purple-800/40 flex items-center justify-between text-[10px] text-slate-300"
                  >
                    <div className="flex items-center space-x-2 truncate">
                      <FileText className="w-3 h-3 text-purple-400 shrink-0" />
                      <span className="text-purple-300 font-bold">[{ev.agent || 'RECON'}]</span>
                      <span className="truncate">{ev.type}: {ev.content?.slice(0, 100) || ev.description}</span>
                    </div>
                    <span className="text-slate-500 shrink-0 ml-2">{ev.timestamp || 'Recent'}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Assistant Thinking / Typing Indicator */}
            {chatSending && (
              <div className="flex items-center space-x-3">
                <div className="w-8 h-8 rounded-lg bg-purple-950 border border-purple-500 flex items-center justify-center text-purple-300 shadow-[0_0_10px_rgba(168,85,247,0.3)]">
                  <Bot className="w-4 h-4 animate-bounce" />
                </div>
                <div className="p-3 rounded-xl bg-obsidian-950 border border-purple-500/40 text-cyber-cyan text-xs flex items-center space-x-2 animate-pulse">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-cyber-cyan" />
                  <span>FORGE Assistant analyzing challenge telemetry & formulating response…</span>
                </div>
              </div>
            )}

            <div ref={chatBottomRef} />
          </div>

          {/* Quick Prompt Suggestions */}
          <div className="flex items-center space-x-2 overflow-x-auto pb-1 text-[11px]">
            <span className="text-slate-500 font-bold text-[10px] uppercase shrink-0">Suggestions:</span>
            {[
              "What is the current run status?",
              "What open ports and web services did recon find?",
              "What is our next exploitation step?",
              "Summarize all recent findings and errors"
            ].map((s, idx) => (
              <button
                key={idx}
                onClick={() => handleSendMessage(s)}
                disabled={chatSending}
                className="px-2.5 py-1 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-800 hover:border-cyber-cyan/50 text-slate-300 hover:text-cyber-cyan text-[10.5px] font-bold whitespace-nowrap transition-all disabled:opacity-40"
              >
                {s}
              </button>
            ))}
          </div>

          {/* Chat Input Bar */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="glass-panel border border-slate-800 p-2.5 rounded-xl flex items-center space-x-2 bg-obsidian-950 shadow-lg"
          >
            <textarea
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSendMessage();
                }
              }}
              rows={2}
              placeholder="Ask Forge AI about this challenge, tool executions, next attack vector, or run telemetry… (Enter to send, Shift+Enter for newline)"
              className="flex-1 bg-obsidian-900 border border-slate-800 rounded-lg p-2.5 text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-cyber-cyan focus:ring-1 focus:ring-cyber-cyan/30 resize-none font-mono custom-scrollbar"
              disabled={chatSending}
            />

            <button
              type="submit"
              disabled={chatSending || !chatInput.trim()}
              className="px-4 py-3 rounded-lg bg-cyber-cyan hover:bg-cyan-300 disabled:opacity-40 disabled:cursor-not-allowed text-obsidian-950 font-display font-bold text-xs uppercase tracking-wider flex items-center space-x-1.5 shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all h-full"
            >
              {chatSending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  <Send className="w-4 h-4" />
                  <span className="hidden sm:inline">SEND</span>
                </>
              )}
            </button>
          </form>
        </div>
      )}

      {/* TAB 1: OVERVIEW */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 font-mono text-xs">
          <div className="lg:col-span-2 space-y-5">
            <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
              <h2 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-2 text-sm neon-text-cyan">
                CHALLENGE OBJECTIVES & HYPOTHESES
              </h2>
              <div className="space-y-3">
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block text-[10px] uppercase font-bold mb-1">Current Primary Objective:</span>
                  <span className="text-cyber-cyan font-bold text-sm">
                    {decisions[0]?.goal || challenge.description || `Autonomous ${challenge.category} surface analysis & flag discovery`}
                  </span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block text-[10px] uppercase font-bold mb-1">Active AI Hypothesis:</span>
                  <span className="text-slate-200 leading-relaxed">
                    {decisions[0]?.reason || findings[0]?.description || 'Target capability reasoning active. Formulating vulnerability hypothesis...'}
                  </span>
                </div>
                {currentPlan?.tasks && currentPlan.tasks.length > 0 && (
                  <div className="bg-obsidian-950 p-3 rounded-lg border border-cyber-cyan/30 flex items-center justify-between">
                    <div>
                      <span className="text-slate-500 block text-[10px] uppercase font-bold mb-0.5">Active Mission Task:</span>
                      <span className="text-cyber-cyan font-bold text-xs">
                        {currentPlan.tasks.find(t => t.status === 'IN_PROGRESS')?.title || currentPlan.tasks[0]?.title}
                      </span>
                    </div>
                    <button
                      onClick={() => handleTabChange('todo_plan')}
                      className="px-2.5 py-1 rounded bg-obsidian-900 border border-cyber-cyan/60 hover:bg-cyan-950 text-cyber-cyan text-[11px] font-bold"
                    >
                      VIEW TODO LIST →
                    </button>
                  </div>
                )}
                {challenge.candidates && challenge.candidates.length > 0 && (
                  <div className="bg-amber-950/40 p-4 rounded-lg border-2 border-amber-500/70 space-y-2.5 shadow-[0_0_20px_rgba(245,158,11,0.15)]">
                    <div className="flex items-center justify-between">
                      <span className="text-amber-400 font-bold text-xs uppercase flex items-center space-x-1.5">
                        <AlertTriangle className="w-4 h-4 text-amber-400" />
                        <span>FLAG CANDIDATES DISCOVERED (UNVERIFIED)</span>
                      </span>
                      <span className="px-2 py-0.5 rounded bg-amber-900 border border-amber-600 text-amber-300 text-[10px] font-bold">
                        {challenge.candidates.length} CANDIDATE(S)
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-300">
                      Unverified candidate strings extracted by swarm workers during execution. Pending verifier assertion:
                    </p>
                    <div className="space-y-1.5">
                      {challenge.candidates.map((cand, idx) => (
                        <div key={idx} className="flex items-center justify-between p-2.5 bg-obsidian-950 rounded border border-amber-800/60 font-mono text-xs">
                          <code className="text-amber-300 font-bold select-all">{cand.flag}</code>
                          <span className="text-[10px] text-slate-400">
                            Worker: <span className="text-cyan-400">{cand.worker || 'SWARM'}</span> {cand.source ? `(${cand.source})` : ''}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>


            <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
              <h2 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-2 text-sm neon-text-cyan">
                TARGET DISCOVERY & SERVICES MATRIX
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-[11px]">
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5">IP / Host:</span>
                  <span className="text-cyber-cyan font-bold text-sm">{target.currentIp} ({target.hostname})</span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5">Platform / CTF:</span>
                  <span className="text-purple-300 font-bold text-xs truncate block">{challenge.platformName || 'FORGE CTF'}</span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5 flex items-center space-x-1">
                    <Folder className="w-3.5 h-3.5 text-cyber-cyan" />
                    <span>Working Directory:</span>
                  </span>
                  <span className="text-cyber-cyan font-bold text-xs truncate block" title={challenge.workingDirectory}>
                    {challenge.workingDirectory || './workspaces/' + challenge.name.toLowerCase().replace(/\s+/g, '_')}
                  </span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5">Discovery Method:</span>
                  <span className="text-slate-200 font-semibold">{target.discoveryMethod}</span>
                </div>
              </div>
              <div className="space-y-2 pt-2">
                <span className="text-slate-400 text-[10px] font-bold uppercase block">Open Target Ports:</span>
                {target.services.map((s) => (
                  <div key={s.port} className="flex justify-between items-center px-3 py-2 bg-obsidian-950 border border-slate-800 rounded-lg text-xs">
                    <span className="text-cyber-cyan font-bold">{s.port}/{s.proto}</span>
                    <span className="text-slate-200 font-semibold">{s.service}</span>
                    <span className="text-slate-400 text-[11px]">{s.version}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
            <h2 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-2 text-sm neon-text-cyan flex items-center justify-between">
              <span>SWARM FLEET HUD</span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-950 border border-cyber-cyan/50 text-cyber-cyan">
                {hasLiveFleet ? `${agents.length} LIVE WORKERS` : '3 PARALLEL WORKERS'}
              </span>
            </h2>
            <div className="space-y-3">
              {/* Live Swarm Workers — real telemetry, or role placeholders on standby */}
              {hasLiveFleet ? (
                agents.map((a) => (
                  <div key={a.id} className="p-3 rounded-lg bg-obsidian-950 border border-cyber-cyan/40 space-y-1">
                    <div className="flex justify-between font-bold text-cyber-cyan text-xs">
                      <span>{a.name} WORKER</span>
                      <span className={fleetStatusClass(a.status)}>{a.status}</span>
                    </div>
                    <div className="text-[10px] text-purple-300 font-mono">Model: {a.selectedModel || 'FORGE Model Router'}</div>
                    <p className="text-[11px] text-slate-400 truncate" title={a.currentObjective}>{a.currentObjective}</p>
                    {a.lastTool && (
                      <p className="text-[10px] text-slate-500 font-mono truncate" title={a.lastTool}>$ {a.lastTool}</p>
                    )}
                    <div className="flex justify-between text-[10px] text-slate-500 pt-0.5">
                      <span>Runtime: <span className="text-slate-300">{a.runtime}</span></span>
                      <span>Cmds: <span className="text-cyber-emerald">{a.actionsCompleted}</span> • Fails: <span className={a.failures > 0 ? 'text-cyber-rose' : 'text-slate-400'}>{a.failures}</span></span>
                    </div>
                  </div>
                ))
              ) : (
                WORKER_ROLES.map((role) => (
                  <div key={role.name} className="p-3 rounded-lg bg-obsidian-950 border border-cyber-cyan/40 space-y-1">
                    <div className="flex justify-between font-bold text-cyber-cyan text-xs">
                      <span>{role.icon} {role.name} WORKER</span>
                      <span className="text-slate-500">STANDBY</span>
                    </div>
                    <div className="text-[10px] text-purple-300 font-mono">Model: {role.model}</div>
                    <p className="text-[11px] text-slate-400">{role.blurb}</p>
                  </div>
                ))
              )}

              {/* Shared Blackboard State Summary */}
              <div className="p-3 rounded-lg bg-obsidian-900/80 border border-slate-700 space-y-1.5 mt-2">
                <span className="text-[10px] text-slate-400 uppercase font-bold block">SHARED BLACKBOARD STATUS:</span>
                <div className="flex justify-between text-[11px] text-slate-300">
                  <span>Discovered Endpoints:</span>
                  <span className="font-bold text-cyber-cyan">{target.services.length > 0 ? target.services.length + ' mapped' : 'Active'}</span>
                </div>
                <div className="flex justify-between text-[11px] text-slate-300">
                  <span>Deduplication Filter:</span>
                  <span className="font-bold text-cyber-emerald">ACTIVE (0 collision)</span>
                </div>
                <div className="flex justify-between text-[11px] text-slate-300">
                  <span>Global Flag Kill-Switch:</span>
                  <span className="font-bold text-amber-400">ARMED (Auto-terminate)</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB: MISSION TODO LIST */}
      {activeTab === 'todo_plan' && (
        <div className="space-y-5 font-mono text-xs">
          {/* Mission Strategy & Header Summary */}
          <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-5 space-y-4 shadow-[0_0_30px_rgba(0,240,255,0.15)] cyber-corner">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
              <div>
                <div className="flex items-center space-x-2">
                  <ListTodo className="w-5 h-5 text-cyber-cyan animate-pulse" />
                  <h2 className="text-lg font-display font-bold text-slate-100 uppercase neon-text-cyan tracking-wider">
                    AUTONOMOUS PRE-FLIGHT MISSION PLAN & TODO LIST
                  </h2>
                </div>
                <p className="text-slate-400 text-xs mt-1">
                  {currentPlan?.summary || `Pre-flight sequential attack vector and tactical milestones for ${target.currentIp}`}
                </p>
              </div>

              <div className="flex items-center space-x-3 shrink-0">
                <div className="bg-obsidian-950 px-3 py-1.5 rounded-lg border border-slate-800 flex items-center space-x-2">
                  <Cpu className="w-4 h-4 text-cyber-emerald" />
                  <span className="text-[11px] text-slate-300 font-bold">
                    Planner: <span className="text-cyber-emerald">{currentPlan?.model || 'AI Model Router'}</span>
                  </span>
                </div>
                <div className="bg-obsidian-950 px-3 py-1.5 rounded-lg border border-slate-800 flex items-center space-x-2">
                  <Zap className="w-4 h-4 text-cyber-cyan" />
                  <span className="text-[11px] text-slate-300 font-bold">
                    Completed: <span className="text-cyber-cyan">
                      {currentPlan?.tasks?.filter(t => t.status === 'COMPLETED').length || 0} / {currentPlan?.tasks?.length || 0}
                    </span>
                  </span>
                </div>
              </div>
            </div>

            {/* Strategic Multi-Model Review Alert Banner (if any reviews occurred) */}
            {currentPlan?.strategic_reviews && currentPlan.strategic_reviews.length > 0 && (
              <div className="p-4 rounded-xl bg-purple-950/40 border-2 border-purple-500/60 shadow-[0_0_20px_rgba(168,85,247,0.2)] space-y-2.5">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div className="flex items-center space-x-2">
                    <Sparkles className="w-4 h-4 text-purple-400 animate-spin" />
                    <span className="font-display font-bold text-purple-200 uppercase tracking-wide text-xs">
                      🤖 MULTI-MODEL STRATEGIC REVIEW & PIVOT ACTIVE
                    </span>
                  </div>
                  <span className="text-[10px] text-purple-300 bg-purple-900/60 px-2 py-0.5 rounded border border-purple-700">
                    Reviewer: {currentPlan.strategic_reviews[currentPlan.strategic_reviews.length - 1].reviewer_model}
                  </span>
                </div>
                <div className="text-[11px] text-slate-300 space-y-1">
                  <p><span className="text-purple-400 font-bold">Diagnosis:</span> {currentPlan.strategic_reviews[currentPlan.strategic_reviews.length - 1].diagnosis}</p>
                  <p><span className="text-cyber-emerald font-bold">Pivot Strategy:</span> {currentPlan.strategic_reviews[currentPlan.strategic_reviews.length - 1].pivot_strategy}</p>
                </div>
              </div>
            )}
          </div>

          {/* Task Checklist Cards */}
          <div className="space-y-3">
            {currentPlan?.tasks && currentPlan.tasks.length > 0 ? (
              currentPlan.tasks.map((task, idx) => {
                const isCompleted = task.status === 'COMPLETED';
                const isInProgress = task.status === 'IN_PROGRESS';
                const isRevised = task.status === 'REVISED';
                const isFailed = task.status === 'FAILED';

                return (
                  <div
                    key={task.id || idx}
                    className={`glass-panel p-4 rounded-xl border transition-all duration-300 ${
                      isInProgress
                        ? 'border-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.25)] bg-obsidian-900/90'
                        : isCompleted
                        ? 'border-emerald-500/40 bg-emerald-950/10 hover:border-emerald-500/70'
                        : isRevised
                        ? 'border-purple-500/40 bg-purple-950/10'
                        : 'border-slate-800/80 bg-obsidian-950/60 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                      <div className="flex items-start space-x-3.5">
                        <div className="pt-0.5 shrink-0">
                          {isCompleted ? (
                            <div className="w-6 h-6 rounded-full bg-emerald-500/20 border border-emerald-500 flex items-center justify-center text-cyber-emerald shadow-[0_0_10px_rgba(0,255,136,0.3)]">
                              <Check className="w-3.5 h-3.5" />
                            </div>
                          ) : isInProgress ? (
                            <div className="w-6 h-6 rounded-full bg-cyan-500/20 border border-cyber-cyan flex items-center justify-center text-cyber-cyan animate-pulse shadow-[0_0_10px_rgba(0,240,255,0.4)]">
                              <div className="w-2 h-2 rounded-full bg-cyber-cyan"></div>
                            </div>
                          ) : isRevised ? (
                            <div className="w-6 h-6 rounded-full bg-purple-500/20 border border-purple-500 flex items-center justify-center text-purple-400">
                              <Sparkles className="w-3.5 h-3.5" />
                            </div>
                          ) : isFailed ? (
                            <div className="w-6 h-6 rounded-full bg-rose-500/20 border border-cyber-rose flex items-center justify-center text-cyber-rose">
                              <AlertTriangle className="w-3.5 h-3.5" />
                            </div>
                          ) : (
                            <div className="w-6 h-6 rounded-full bg-obsidian-950 border border-slate-700 flex items-center justify-center text-slate-500 text-[10px] font-bold">
                              {idx + 1}
                            </div>
                          )}
                        </div>

                        <div className="space-y-1">
                          <div className="flex items-center space-x-2 flex-wrap">
                            <span className="font-bold text-sm text-slate-100 tracking-wide">{task.title}</span>
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                              task.phase === 'RECON'
                                ? 'bg-blue-950/80 text-blue-400 border border-blue-800'
                                : task.phase === 'SURFACE_ANALYSIS'
                                ? 'bg-amber-950/80 text-amber-400 border border-amber-800'
                                : task.phase === 'EXPLOITATION'
                                ? 'bg-purple-950/80 text-purple-400 border border-purple-800'
                                : task.phase === 'FLAG_EXTRACTION'
                                ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800'
                                : 'bg-slate-900 text-slate-300 border border-slate-700'
                            }`}>
                              {task.phase}
                            </span>
                          </div>
                          <p className="text-slate-400 text-xs leading-relaxed">{task.reasoning}</p>
                        </div>
                      </div>

                      <div className="flex items-center space-x-2 self-start sm:self-center shrink-0">
                        <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold tracking-wider uppercase border ${
                          isCompleted
                            ? 'bg-emerald-950/90 text-emerald-400 border-emerald-700'
                            : isInProgress
                            ? 'bg-cyan-950/90 text-cyber-cyan border-cyber-cyan shadow-[0_0_10px_rgba(0,240,255,0.3)] animate-pulse'
                            : isRevised
                            ? 'bg-purple-950/90 text-purple-300 border-purple-700'
                            : isFailed
                            ? 'bg-rose-950/90 text-cyber-rose border-rose-800'
                            : 'bg-obsidian-950 text-slate-500 border-slate-800'
                        }`}>
                          {task.status}
                        </span>
                      </div>
                    </div>

                    {/* Tool & Playbook Details */}
                    <div className="mt-3 pt-3 border-t border-slate-800/60 flex items-center justify-between flex-wrap gap-2 text-[11px]">
                      <div className="flex items-center space-x-2 text-slate-400">
                        <Terminal className="w-3.5 h-3.5 text-cyber-cyan" />
                        <span>Tool: <code className="text-cyber-cyan font-semibold">{task.tool}</code></span>
                        {task.playbook_ref && (
                          <span className="ml-2 text-slate-500">• Playbook: <code className="text-slate-300">{task.playbook_ref}</code></span>
                        )}
                      </div>
                      {task.output_summary && (
                        <div className="text-slate-400 text-[10px] truncate max-w-md" title={task.output_summary}>
                          Output: <span className="text-slate-200">{task.output_summary}</span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="glass-panel border border-slate-800 rounded-xl p-8 text-center space-y-3">
                <ListTodo className="w-8 h-8 text-cyber-cyan mx-auto animate-pulse" />
                <h3 className="font-display font-bold text-slate-200">GENERATING PRE-FLIGHT MISSION TODO LIST</h3>
                <p className="text-slate-400 text-xs max-w-md mx-auto font-mono">
                  FORGE Autonomous Agent is analyzing target surface scope and compiling initial attack tasks.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: WORKFLOW PIPELINE */}
      {activeTab === 'workflow' && (
        <div className="space-y-6 font-mono text-xs">
          <div className="glass-panel border border-slate-800 p-5 rounded-xl">
            <h2 className="font-display font-bold text-slate-100 uppercase tracking-wider mb-1 text-sm neon-text-cyan">
              VISUAL INVESTIGATION PIPELINE
            </h2>
            <p className="text-slate-400">Interactive CTF attack workflow diagram. Click a node to inspect step telemetry.</p>
          </div>

          {/* Node Flow Horizontal Pipeline */}
          <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-6 overflow-x-auto shadow-inner">
            <div className="flex items-center space-x-3 min-w-max">
              {activeWorkflowNodes.map((node, idx) => (
                <React.Fragment key={node.id}>
                  <div
                    onClick={() => { soundEngine.playClick(); setSelectedWorkflowNode(node); }}
                    className={`p-4 rounded-xl border cursor-pointer transition-all hover:scale-105 ${
                      selectedWorkflowNode?.id === node.id
                        ? 'bg-cyber-cyan/20 border-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.4)]'
                        : node.status === 'COMPLETED'
                        ? 'bg-emerald-950/30 border-cyber-emerald/60 text-slate-200'
                        : node.status === 'ACTIVE'
                        ? 'bg-cyan-950/30 border-cyber-cyan text-cyber-cyan'
                        : 'bg-obsidian-900/40 border-slate-800 text-slate-500'
                    }`}
                  >
                    <div className="flex items-center space-x-2 mb-2">
                      <span className={`w-2.5 h-2.5 rounded-full ${
                        node.status === 'COMPLETED' ? 'bg-cyber-emerald' :
                        node.status === 'ACTIVE' ? 'bg-cyber-cyan animate-ping' :
                        'bg-slate-600'
                      }`}></span>
                      <span className="font-bold text-xs font-display">{node.label}</span>
                    </div>
                    <span className="text-[9px] px-2 py-0.5 rounded font-bold uppercase block text-center border border-slate-800 bg-obsidian-950">
                      {node.status}
                    </span>
                  </div>

                  {idx < activeWorkflowNodes.length - 1 && (
                    <ArrowRight className="w-5 h-5 text-slate-600 shrink-0" />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>

          {/* Node Drawer Detail */}
          {selectedWorkflowNode && (
            <div className="glass-panel border-2 border-cyber-cyan/50 rounded-xl p-5 space-y-3 cyber-corner">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <span className="font-display font-bold text-cyber-cyan text-sm">{selectedWorkflowNode.label} NODE TELEMETRY</span>
                <span className="px-2.5 py-1 rounded bg-emerald-950 border border-emerald-800 text-cyber-emerald text-[10px] font-bold">
                  {selectedWorkflowNode.status}
                </span>
              </div>
              <p className="text-slate-300 text-xs leading-relaxed">{selectedWorkflowNode.description}</p>
            </div>
          )}
        </div>
      )}

      {/* TAB 3: TERMINAL */}
      {activeTab === 'terminal' && (
        <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-5 font-mono text-xs space-y-4 shadow-inner">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <span className="font-display font-bold text-cyber-cyan text-sm">TERMINAL STREAM FOR {challenge.name}</span>
            <span className="text-[10px] text-slate-400 font-bold">{logs.length} COMMANDS LOGGED</span>
          </div>

          <div className="space-y-3 max-h-96 overflow-y-auto">
            {logs.map((l) => (
              <div key={l.id} className="p-3.5 bg-obsidian-900 border border-slate-800/80 rounded-lg space-y-1.5 hover:border-cyber-cyan/30 transition-colors">
                <div className="flex justify-between text-[10px] text-slate-400">
                  <span>[{l.timestamp}] {l.type}</span>
                  <span>EXIT: {l.exitCode} • DURATION: {l.duration}</span>
                </div>
                <div className="text-cyber-cyan font-bold font-mono">forge@parrot:~$ {l.command}</div>
                <pre className="text-slate-300 text-[11px] whitespace-pre-wrap bg-obsidian-950 p-2.5 rounded border border-slate-900">{l.output}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 4: AI DECISIONS */}
      {activeTab === 'ai_decisions' && (
        <div className="space-y-4 font-mono text-xs">
          {decisions.map((d) => (
            <div key={d.id} className="glass-panel border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyber-cyan font-display text-sm">{d.agent} • {d.goal}</span>
                <span className="text-cyber-emerald font-bold">{d.confidence}% CONFIDENCE</span>
              </div>
              <p className="text-slate-300"><span className="text-slate-500 font-bold">Capability:</span> {d.capability} → <code className="text-cyber-cyan font-bold">{d.selectedTool || 'bash_cli'}</code></p>
              <p className="text-slate-300 bg-obsidian-950 p-3 rounded-lg border border-slate-900 leading-relaxed font-mono">{d.result}</p>
              
              {/* Collapsible Full AI Prompt & Response Inspector */}
              <details className="mt-2 text-[11px] bg-obsidian-950 p-3 rounded-lg border border-slate-800 cursor-pointer">
                <summary className="text-cyber-cyan font-bold hover:underline select-none flex items-center space-x-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-cyber-cyan" />
                  <span>[ INSPECT FULL RAW PROMPT & MODEL CONTEXT ]</span>
                </summary>
                <div className="mt-3 space-y-2 text-slate-300 font-mono">
                  <div className="flex items-center space-x-2">
                    <span className="text-slate-500 font-bold uppercase text-[10px]">Model Provider:</span>
                    <span className="text-amber-400 font-bold px-2 py-0.5 rounded bg-amber-950/60 border border-amber-800">{d.model || 'model_router'}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 font-bold uppercase block text-[10px] mb-1">Raw Output / Selected Action:</span>
                    <pre className="bg-obsidian-900 p-2.5 rounded border border-slate-800 text-slate-200 whitespace-pre-wrap text-[10.5px] max-h-48 overflow-y-auto custom-scrollbar">{d.result}</pre>
                  </div>
                </div>
              </details>
            </div>
          ))}
        </div>
      )}

      {/* TAB 5: EVIDENCE */}
      {activeTab === 'evidence' && (
        <div className="space-y-4 font-mono text-xs">
          {challenge.derivedArtifacts && challenge.derivedArtifacts.length > 0 && (
            <div className="glass-panel border-2 border-cyber-cyan/60 rounded-xl p-5 space-y-3 shadow-[0_0_20px_rgba(0,240,255,0.15)]">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <span className="font-display font-bold text-cyber-cyan text-sm uppercase flex items-center space-x-2">
                  <FileCode className="w-4 h-4 text-cyber-cyan" />
                  <span>RECONSTRUCTED ENCODED ARTIFACTS</span>
                </span>
                <span className="px-2.5 py-0.5 rounded bg-cyan-950 border border-cyber-cyan text-cyber-cyan text-[10px] font-bold">
                  {challenge.derivedArtifacts.length} RECONSTRUCTED
                </span>
              </div>
              <p className="text-slate-300 text-[11px]">
                Deterministic reconstruction output extracted from encoded tool results/attachments and escalated for specialist analysis:
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
                {challenge.derivedArtifacts.map((art, idx) => (
                  <div key={idx} className="p-3 bg-obsidian-950 rounded-lg border border-slate-800 space-y-2">
                    <div className="flex justify-between items-center text-xs">
                      <span className="font-bold text-cyber-cyan truncate" title={art.filename || art.path}>
                        {art.filename || 'reconstructed_file'}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 font-bold uppercase">
                        {art.status || 'RECONSTRUCTED'}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400 space-y-1">
                      <div>Path: <code className="text-slate-200 select-all">{art.path || 'derived_artifacts/'}</code></div>
                      <div>Type: <span className="text-purple-300 font-bold">{art.artifact_type || 'binary'}</span> {art.size_bytes ? `(${art.size_bytes} bytes)` : ''}</div>
                      {art.worker_id && <div>Reconstructed By: <span className="text-cyan-300 font-bold">{art.worker_id}</span></div>}
                    </div>
                    {art.preview && art.preview.startsWith('data:image/') && (
                      <div className="mt-2 border border-slate-800 rounded p-1 bg-black">
                        <img src={art.preview} alt={art.filename} className="max-h-36 object-contain mx-auto rounded" />
                      </div>
                    )}
                    {art.preview && !art.preview.startsWith('data:image/') && (
                      <pre className="mt-2 p-2 bg-obsidian-900 rounded border border-slate-800 text-[10px] text-slate-300 max-h-24 overflow-y-auto whitespace-pre-wrap select-all">
                        {art.preview.slice(0, 300)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {evidenceList.map((e) => (
            <div key={e.id} className="glass-panel border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyber-cyan font-display text-sm">{e.type} • {e.description}</span>
                <span className="text-slate-400">{e.timestamp}</span>
              </div>
              <pre className="bg-obsidian-950 p-4 rounded-lg text-slate-300 overflow-x-auto whitespace-pre-wrap border border-slate-900">{e.content}</pre>
              <div className="flex justify-end space-x-2 pt-1">
                <button onClick={() => handleCopyText(e.id, e.content)} className="px-3 py-1.5 rounded-lg bg-obsidian-900 border border-slate-700 text-slate-300 hover:text-cyber-cyan text-xs font-bold transition-colors">
                  {copiedId === e.id ? 'COPIED' : 'COPY EVIDENCE'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}


      {/* TAB 6: FINDINGS */}
      {activeTab === 'findings' && (
        <div className="space-y-4 font-mono text-xs">
          {findings.map((f) => (
            <div key={f.id} className="glass-panel border-2 border-cyber-rose/50 rounded-xl p-5 space-y-3 shadow-[0_0_20px_rgba(255,42,109,0.15)] cyber-corner">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyber-rose text-sm font-display">{f.severity}: {f.title}</span>
                <span className="px-2.5 py-1 rounded bg-emerald-950 border border-emerald-800 text-cyber-emerald font-bold">{f.status}</span>
              </div>
              <p className="text-slate-300"><span className="text-slate-500 font-bold">Endpoint:</span> <code className="text-cyber-cyan font-bold">{f.endpoint}</code></p>
              <p className="text-slate-300 leading-relaxed bg-obsidian-950 p-3 rounded-lg border border-slate-900">{f.description}</p>
            </div>
          ))}
        </div>
      )}

      {/* TAB 7: README / WRITEUP */}
      {activeTab === 'readme' && (
        <div className="glass-panel border border-slate-800 rounded-xl p-6 space-y-4 font-mono text-xs">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
            <h2 className="font-display font-bold text-slate-100 uppercase tracking-wider text-sm neon-text-cyan">
              CTF WRITEUP EDITOR & MARKDOWN GENERATOR
            </h2>
            <div className="flex items-center space-x-2">
              <button
                onClick={() => handleGenerateWriteup(true)}
                disabled={writeupBusy}
                className="px-3.5 py-2 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/30 border border-cyber-cyan/60 text-cyber-cyan font-bold flex items-center space-x-1.5 transition-colors disabled:opacity-50"
                title={writeupSaved ? 'Author a FRESH writeup, replacing the saved one shown below (save to persist)' : 'Author a technical writeup from the run telemetry'}
              >
                <Sparkles className={`w-4 h-4 text-cyber-cyan ${writeupBusy ? 'animate-pulse' : ''}`} />
                <span>{writeupBusy ? 'CRAFTING…' : (writeupSaved ? 'REGENERATE' : 'AUTO WRITEUP')}</span>
              </button>
              <button
                onClick={handleSaveWriteup}
                disabled={writeupBusy || !writeupText}
                className="px-3.5 py-2 rounded-lg bg-cyber-emerald/15 hover:bg-cyber-emerald/30 border border-cyber-emerald/60 text-cyber-emerald font-bold flex items-center space-x-1.5 transition-colors disabled:opacity-50"
                title="Save the writeup markdown into the challenge working folder"
              >
                <Save className="w-4 h-4" />
                <span>SAVE MD</span>
              </button>
              <button
                onClick={handleCopyWriteup}
                className="px-3.5 py-2 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-200 font-bold flex items-center space-x-1.5 transition-colors"
              >
                {copiedReadme ? <Check className="w-4 h-4 text-cyber-emerald" /> : <Copy className="w-4 h-4" />}
                <span>{copiedReadme ? 'COPIED' : 'COPY MD'}</span>
              </button>
            </div>
          </div>

          {(writeupBy || writeupSaveMsg || writeupSaved) && (
            <div className="flex items-center justify-between text-[11px] font-mono">
              <span className="text-slate-500">
                {writeupSaved
                  ? '✓ showing SAVED writeup — REGENERATE to author a fresh draft'
                  : (writeupBy ? `generated by ${writeupBy}` : '')}
              </span>
              <span className="text-cyber-emerald">{writeupSaveMsg}</span>
            </div>
          )}

          <textarea
            rows={16}
            value={writeupText}
            placeholder={writeupBusy ? 'Crafting writeup with AI…' : 'Click AUTO WRITEUP to generate a technical writeup from the run telemetry.'}
            onChange={(e) => setWriteupText(e.target.value)}
            className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-4 text-slate-200 font-mono text-xs focus:outline-none focus:border-cyber-cyan leading-relaxed shadow-inner"
          ></textarea>
        </div>
      )}
    </div>
  );
};

