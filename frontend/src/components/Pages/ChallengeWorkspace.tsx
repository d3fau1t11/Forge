import React, { useState, useEffect } from 'react';
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
  Zap
} from 'lucide-react';
import { Challenge, Target, EvidenceItem, AiDecision, TerminalLog, Finding, WorkflowNode } from '../../types';
import { soundEngine } from '../../utils/soundEngine';
import { computeElapsedSeconds, formatDuration } from '../../utils/timeUtils';

interface ChallengeWorkspaceProps {
  challenge: Challenge;
  target: Target;
  evidenceList: EvidenceItem[];
  decisions: AiDecision[];
  logs: TerminalLog[];
  findings: Finding[];
  workflowNodes: WorkflowNode[];
  onBackToChallenges: () => void;
  onToggleStatus: (id: string) => void;
}

export const ChallengeWorkspace: React.FC<ChallengeWorkspaceProps> = ({
  challenge,
  target,
  evidenceList,
  decisions,
  logs,
  findings,
  workflowNodes,
  onBackToChallenges,
  onToggleStatus
}) => {
  const [activeTab, setActiveTab] = useState<
    'overview' | 'todo_plan' | 'workflow' | 'terminal' | 'ai_decisions' | 'evidence' | 'findings' | 'readme'
  >('overview');

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

  const buildDynamicWriteup = () => {
    const evidenceText = evidenceList.map(e => `### [${e.source}] ${e.description}\n\`\`\`text\n${e.content}\n\`\`\``).join('\n\n') || '*No evidence collected yet.*';
    const findingsText = findings.map(f => `- **${f.severity}**: ${f.title} — ${f.description}`).join('\n') || '*No findings logged yet.*';
    return `# CTF WRITEUP: ${challenge.name} (${challenge.category})

## Challenge Information
- **Platform / Competition**: ${challenge.platformName || 'FORGE CTF Framework'}
- **Category**: ${challenge.category}
- **Target Address**: ${target.currentIp} (${target.hostname})
- **Working Directory**: \`${challenge.workingDirectory || './workspaces/' + challenge.name.toLowerCase().replace(/\s+/g, '_')}\`
- **Difficulty**: ${challenge.difficulty}
- **Status**: ${challenge.status}
- **Flag**: ${challenge.flag || (challenge.flagStatus === 'CAPTURED' ? 'FORGE{flag_captured}' : 'Pending / Unfound')}

## 1. Reconnaissance & Investigation Strategy
The FORGE Autonomous Framework conducted targeted analysis on \`${target.currentIp}\`.

## 2. Key Findings & Vulnerability Assessment
${findingsText}

## 3. Collected Telemetry & Evidence Artifacts
${evidenceText}

## 4. Flag Extraction & Verification
- Status: **${challenge.flagStatus}**
- Flag: \`${challenge.flag || 'In Progress'}\`
`;
  };

  // Writeup content state
  const [writeupText, setWriteupText] = useState<string>(buildDynamicWriteup());

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

  const handleGenerateWriteup = () => {
    soundEngine.playSuccess();
    setWriteupText(buildDynamicWriteup());
  };

  const currentPlan = challenge.missionPlan || challenge.mission_plan;

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-10">
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
              <div className="flex items-center space-x-3 flex-wrap">
                <h1 className="text-2xl font-display font-bold tracking-wider text-slate-100 neon-text-cyan">{challenge.name}</h1>
                <span className="px-2.5 py-1 rounded bg-cyan-950/80 border border-cyber-cyan/60 text-cyber-cyan text-xs font-bold uppercase">
                  {challenge.category} CTF
                </span>
                <span className="px-2.5 py-0.5 rounded bg-emerald-950/80 border border-emerald-700 text-emerald-400 text-[11px] font-bold font-mono">
                  🐧 OS: Linux (Parrot/Kali) — Optimal Operational Mode
                </span>
                {challenge.status === 'RUNNING' && (
                  <span className="px-2.5 py-0.5 rounded bg-amber-950/90 border border-amber-500/80 text-amber-300 text-[11px] font-bold font-mono flex items-center space-x-1 animate-pulse shadow-[0_0_10px_rgba(245,158,11,0.3)]">
                    <Zap className="w-3 h-3 text-amber-400 fill-amber-400" />
                    <span>⚡ KEEP-AWAKE: Sleep Prevention Active</span>
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Action Controls & Uptime & Progress */}
          <div className="flex items-center space-x-4 flex-wrap gap-2">
            <div className="flex items-center space-x-2 text-xs bg-obsidian-900/90 px-3 py-1.5 rounded-lg border border-slate-800 shadow-inner">
              <Clock className="w-3.5 h-3.5 text-cyber-cyan" />
              <span className="text-slate-400 font-bold text-[10px] uppercase">UPTIME:</span>
              <span className="text-slate-100 font-bold font-mono text-xs">{formatDuration(elapsedSeconds)}</span>
            </div>

            <div className="flex items-center space-x-2 text-xs">
              <span className={`w-3 h-3 rounded-full ${challenge.status === 'RUNNING' ? 'bg-cyber-emerald animate-ping' : 'bg-cyber-amber'}`}></span>
              <span className="font-bold text-cyber-emerald tracking-wider">● {challenge.status}</span>
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

      {/* 8 Workspace Navigation Tabs */}
      <div className="flex items-center space-x-2 border-b border-slate-800 pb-2 overflow-x-auto text-xs font-mono">
        {[
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
              {t.key === 'todo_plan' && currentPlan?.tasks && (
                <span className="ml-1 px-1.5 py-0.2 rounded-full bg-obsidian-950 text-[10px] text-cyber-cyan font-bold border border-cyber-cyan/40">
                  {currentPlan.tasks.filter(tk => tk.status === 'COMPLETED').length}/{currentPlan.tasks.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

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
              <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-950 border border-cyber-cyan/50 text-cyber-cyan">3 PARALLEL WORKERS</span>
            </h2>
            <div className="space-y-3">
              {/* Swarm Worker 1 */}
              <div className="p-3 rounded-lg bg-obsidian-950 border border-cyber-cyan/40 space-y-1">
                <div className="flex justify-between font-bold text-cyber-cyan text-xs">
                  <span>🛰️ RECON WORKER</span>
                  <span className={challenge.status === 'RUNNING' ? 'text-cyber-emerald animate-pulse' : 'text-slate-500'}>
                    {challenge.status === 'RUNNING' ? 'CRAWLING' : 'STANDBY'}
                  </span>
                </div>
                <div className="text-[10px] text-purple-300 font-mono">Model: Groq Qwen / Minimax M3 (Zero Cost)</div>
                <p className="text-[11px] text-slate-400">Port scanning, directory fuzzing & endpoint harvesting</p>
              </div>

              {/* Swarm Worker 2 */}
              <div className="p-3 rounded-lg bg-obsidian-950 border border-cyber-cyan/40 space-y-1">
                <div className="flex justify-between font-bold text-cyber-cyan text-xs">
                  <span>🔬 CODE & CRYPTO AUDITOR</span>
                  <span className={challenge.status === 'RUNNING' ? 'text-cyber-cyan animate-pulse' : 'text-slate-500'}>
                    {challenge.status === 'RUNNING' ? 'DEOBFUSCATING' : 'STANDBY'}
                  </span>
                </div>
                <div className="text-[10px] text-purple-300 font-mono">Model: Mistral Codestral (Specialist)</div>
                <p className="text-[11px] text-slate-400">Decompiling scripts, ROT13/JWT token & comment decoding</p>
              </div>

              {/* Swarm Worker 3 */}
              <div className="p-3 rounded-lg bg-obsidian-950 border border-cyber-cyan/40 space-y-1">
                <div className="flex justify-between font-bold text-cyber-cyan text-xs">
                  <span>⚡ EXPLOIT & PWN SOLVER</span>
                  <span className={challenge.status === 'RUNNING' ? 'text-cyber-amber animate-pulse' : 'text-slate-500'}>
                    {challenge.status === 'RUNNING' ? 'SYNTHESIZING' : 'STANDBY'}
                  </span>
                </div>
                <div className="text-[10px] text-purple-300 font-mono">Model: xKiro Qwen Coder / DeepSeek V4</div>
                <p className="text-[11px] text-slate-400">Crafting auth bypass headers, SQLi & payload delivery</p>
              </div>

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
                onClick={handleGenerateWriteup}
                className="px-3.5 py-2 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/30 border border-cyber-cyan/60 text-cyber-cyan font-bold flex items-center space-x-1.5 transition-colors"
              >
                <Sparkles className="w-4 h-4 text-cyber-cyan" />
                <span>AUTO WRITEUP</span>
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

          <textarea
            rows={16}
            value={writeupText}
            onChange={(e) => setWriteupText(e.target.value)}
            className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-4 text-slate-200 font-mono text-xs focus:outline-none focus:border-cyber-cyan leading-relaxed shadow-inner"
          ></textarea>
        </div>
      )}
    </div>
  );
};

