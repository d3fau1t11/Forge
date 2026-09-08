import { useState, useEffect } from 'react';
import { NavTab, Challenge, Target, AgentInfo, ToolItem, AiDecision, ModelRoute, EvidenceItem, TerminalLog, ProviderInfo, CheckpointItem, AuditLog, Finding, WorkflowNode } from './types';
import { 
  INITIAL_CHALLENGES, 
  INITIAL_TARGETS, 
  INITIAL_AGENTS, 
  INITIAL_TOOLS, 
  INITIAL_AI_DECISIONS, 
  INITIAL_MODEL_ROUTES, 
  INITIAL_EVIDENCE, 
  INITIAL_TERMINAL_LOGS, 
  INITIAL_PROVIDERS, 
  INITIAL_CHECKPOINTS, 
  INITIAL_AUDIT_LOGS, 
  INITIAL_FINDINGS,
  INITIAL_WORKFLOW_PIPELINE 
} from './data/initialState';

import { Sidebar } from './components/Shell/Sidebar';
import { TopBar } from './components/Shell/TopBar';
import { EmergencyStopModal } from './components/Shell/EmergencyStopModal';
import { PackageInstallModal, PackageInstallRequest } from './components/Shell/PackageInstallModal';
import { RootPermissionModal, RootPermissionRequest } from './components/Shell/RootPermissionModal';

import { CommandCenter } from './components/Pages/CommandCenter';
import { Challenges } from './components/Pages/Challenges';
import { Targets } from './components/Pages/Targets';
import { Agents } from './components/Pages/Agents';
import { Tools } from './components/Pages/Tools';
import { AiIntelligence } from './components/Pages/AiIntelligence';
import { PlaybooksVault } from './components/Pages/PlaybooksVault';
import { Evidence } from './components/Pages/Evidence';
import { TerminalView } from './components/Pages/TerminalView';
import { Providers } from './components/Pages/Providers';
import { SystemView } from './components/Pages/SystemView';
import { ChallengeWorkspace } from './components/Pages/ChallengeWorkspace';
import { KnowledgeCoverage } from './components/Pages/KnowledgeCoverage';
import { apiService } from './services/api';
import { AlertTriangle, X } from 'lucide-react';
import { soundEngine } from './utils/soundEngine';
import { formatDuration, parseUtcMs } from './utils/timeUtils';

const SWARM_AGENT_NAMES: Record<string, string> = {
  worker_recon: 'RECON',
  worker_code_crypto: 'CRYPTO',
  worker_exploit_pwn: 'PWN'
};

// Map live swarm worker state (from /api/agents + AGENT_UPDATE events) into AgentInfo cards.
function mapSwarmAgents(swarmAgents: any[]): AgentInfo[] {
  return swarmAgents.map((a: any) => {
    const statusRaw = String(a.status || 'IDLE').toUpperCase();
    const status = (['RUNNING', 'ANALYZING', 'IDLE', 'STANDBY', 'FAILED'] as const).includes(statusRaw as any)
      ? (statusRaw as AgentInfo['status'])
      : 'IDLE';
    return {
      id: a.worker_id || a.id || `swarm-${Math.random().toString(36).slice(2, 8)}`,
      name: (SWARM_AGENT_NAMES[a.worker_id] || a.worker_id || 'AGENT').toUpperCase() as AgentInfo['name'],
      status,
      currentObjective: a.current_task || 'Idle — waiting for tasks',
      currentCapability: a.current_capability || 'swarm',
      selectedModel: a.selected_model || 'FORGE Model Router',
      lastTool: a.last_tool || '',
      lastResult: a.last_result || '',
      runtime: formatDuration(Number(a.runtime_seconds) || 0),
      actionsCompleted: Number(a.commands_run) || 0,
      failures: Number(a.failures) || 0,
      checkpointStatus: status === 'FAILED' ? 'DEGRADED' : (Number(a.commands_run) > 0 ? 'ACTIVE' : 'IDLE'),
      challengeId: a.challenge_id
    };
  });
}

// Map persisted ToolExecutionModel rows (from /api/tools/executions) into TerminalLog entries.
function mapToolExecutionsToLogs(executions: any[]): TerminalLog[] {
  return executions.map((e: any) => ({
    id: e.id || `exec-${Date.now()}-${Math.random()}`,
    timestamp: e.created_at ? new Date(e.created_at).toLocaleTimeString() : new Date().toLocaleTimeString(),
    command: e.command || '',
    output: (e.stdout || e.stderr || `[Exit ${e.exit_code}] no output`).trim(),
    exitCode: e.exit_code ?? -1,
    duration: e.duration_ms != null ? `${(Number(e.duration_ms) / 1000).toFixed(1)}s` : '—',
    type: 'EXECUTION',
    agent: e.agent,
    challengeId: e.challenge_id || undefined
  }));
}

export default function App() {
  const [activeTab, setActiveTab] = useState<NavTab>('command');
  const [activeChallenge, setActiveChallenge] = useState<Challenge | null>(null);
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [showModalKillSwitch, setShowModalKillSwitch] = useState(false);
  const [operationalMode, setOperationalMode] = useState<string>('CTF_OFFENSIVE_CONTROLLED');
  const [fallbackNotice, setFallbackNotice] = useState<{
    failedProvider: string;
    reason: string;
    nextProvider: string;
    timestamp: string;
  } | null>(null);

  // Application Data States
  const [challenges, setChallenges] = useState<Challenge[]>(INITIAL_CHALLENGES);
  // Active HITL checkpoint reports keyed by challenge id (set on CHECKPOINT_REACHED).
  const [checkpointReports, setCheckpointReports] = useState<Record<string, { cycle: number; report: string }>>({});
  const [targets, setTargets] = useState<Target[]>(INITIAL_TARGETS);
  const [agents, setAgents] = useState<AgentInfo[]>(INITIAL_AGENTS);
  const [tools, setTools] = useState<ToolItem[]>(INITIAL_TOOLS);
  const [decisions, setDecisions] = useState<AiDecision[]>(INITIAL_AI_DECISIONS);
  const [routes] = useState<ModelRoute[]>(INITIAL_MODEL_ROUTES);
  const [evidenceList, setEvidenceList] = useState<EvidenceItem[]>(INITIAL_EVIDENCE);
  const [terminalLogs, setTerminalLogs] = useState<TerminalLog[]>(INITIAL_TERMINAL_LOGS);
  const [providers, setProviders] = useState<ProviderInfo[]>(INITIAL_PROVIDERS);
  const [checkpoints] = useState<CheckpointItem[]>(INITIAL_CHECKPOINTS);
  const [auditLogs] = useState<AuditLog[]>(INITIAL_AUDIT_LOGS);
  const [findings, setFindings] = useState<Finding[]>(INITIAL_FINDINGS);
  const [workflowNodes] = useState<WorkflowNode[]>(INITIAL_WORKFLOW_PIPELINE);
  const [packageRequests, setPackageRequests] = useState<PackageInstallRequest[]>([]);
  const [rootRequests, setRootRequests] = useState<RootPermissionRequest[]>([]);
  const [knowledgeRefreshTrigger, setKnowledgeRefreshTrigger] = useState(0);

  useEffect(() => {
    fetchBackendData();

    // Setup live WebSocket listener
    let ws: WebSocket | null = null;
    try {
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsHost = window.location.host || 'localhost:8000';
      ws = new WebSocket(`${wsProtocol}//${wsHost}/ws/events`);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === 'RUN_STARTED') {
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, status: 'RUNNING' } : c))
            );
            setActiveChallenge((prev) => (prev && prev.id === data.challenge_id ? { ...prev, status: 'RUNNING' } : prev));
          } else if (data.event === 'PLAN_GENERATED' || data.event === 'PLAN_UPDATED') {
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, missionPlan: data.plan, mission_plan: data.plan } : c))
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id ? { ...prev, missionPlan: data.plan, mission_plan: data.plan } : prev
            );
          } else if (data.event === 'STRATEGIC_REVIEW_TRIGGERED') {
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, missionPlan: data.plan, mission_plan: data.plan } : c))
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id ? { ...prev, missionPlan: data.plan, mission_plan: data.plan } : prev
            );
          } else if (data.event === 'PROGRESS_UPDATED') {
            setChallenges((prev) =>
              prev.map((c) =>
                c.id === data.challenge_id
                  ? {
                      ...c,
                      progress: data.progress,
                      status: 'RUNNING',
                      durationSeconds: data.duration_seconds ?? c.durationSeconds,
                      duration_seconds: data.duration_seconds ?? c.duration_seconds
                    }
                  : c
              )
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id
                ? {
                    ...prev,
                    progress: data.progress,
                    status: 'RUNNING',
                    durationSeconds: data.duration_seconds ?? prev.durationSeconds,
                    duration_seconds: data.duration_seconds ?? prev.duration_seconds
                  }
                : prev
            );
          } else if (data.event === 'LOG_OUTPUT') {
            const newLog: TerminalLog = {
              id: `log-${Date.now()}-${Math.random()}`,
              timestamp: data.timestamp || new Date().toLocaleTimeString(),
              command: data.command,
              output: data.output,
              exitCode: data.exit_code,
              duration: '1.2s',
              type: 'EXECUTION',
              challengeId: data.challenge_id
            };
            setTerminalLogs((prev) => [newLog, ...prev]);
          } else if (data.event === 'AI_DECISION') {
            const newDecision: AiDecision = {
              id: `dec-${Date.now()}`,
              timestamp: new Date().toLocaleTimeString(),
              agent: data.agent,
              goal: data.goal,
              capability: data.capability,
              selectedTool: data.capability,
              result: data.result,
              confidence: data.confidence || 90,
              costUsd: 0,
              model: data.model,
              challengeId: data.challenge_id
            };
            setDecisions((prev) => [newDecision, ...prev]);
          } else if (data.event === 'AI_PROMPT_TRANSPARENCY') {
            const newDecision: AiDecision = {
              id: `dec-prompt-${Date.now()}`,
              timestamp: new Date().toLocaleTimeString(),
              agent: 'ORCHESTRATOR',
              goal: `Turn #${data.turn} AI Prompt & Strategy Generation`,
              capability: '1-command-react-loop',
              selectedTool: 'bash_cli',
              result: data.raw_response,
              confidence: 95,
              costUsd: 0,
              model: data.model,
              challengeId: data.challenge_id
            };
            setDecisions((prev) => [newDecision, ...prev]);
          } else if (data.event === 'EVIDENCE_CAPTURED') {
            const newEv: EvidenceItem = {
              id: data.evidence_id || `ev-${Date.now()}`,
              timestamp: new Date().toLocaleTimeString(),
              agent: 'RECON',
              type: data.type || 'command_output',
              source: data.source || 'tool',
              description: data.description || 'Captured Telemetry',
              content: `Evidence generated for challenge ${data.challenge_id}`,
              hash: `sha256-${Date.now()}`,
              challengeId: data.challenge_id
            };
            setEvidenceList((prev) => [newEv, ...prev]);
          } else if (data.event === 'FLAG_CAPTURED') {
            setChallenges((prev) =>
              prev.map((c) =>
                c.id === data.challenge_id
                  ? {
                      ...c,
                      flagStatus: 'CAPTURED',
                      flag: data.flag,
                      status: 'COMPLETED',
                      progress: 100,
                      durationSeconds: data.duration_seconds ?? c.durationSeconds,
                      duration_seconds: data.duration_seconds ?? c.duration_seconds
                    }
                  : c
              )
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id
                ? {
                    ...prev,
                    flagStatus: 'CAPTURED',
                    flag: data.flag,
                    status: 'COMPLETED',
                    progress: 100,
                    durationSeconds: data.duration_seconds ?? prev.durationSeconds,
                    duration_seconds: data.duration_seconds ?? prev.duration_seconds
                  }
                : prev
            );
            const newFinding: Finding = {
              id: `find-${Date.now()}`,
              title: `Flag Extracted`,
              severity: 'CRITICAL',
              endpoint: 'Target System',
              status: 'VERIFIED',
              description: `Successfully extracted flag: ${data.flag}`,
              challengeId: data.challenge_id
            };
            setFindings((prev) => [newFinding, ...prev]);
          } else if (data.event === 'RUN_COMPLETED') {
            setChallenges((prev) =>
              prev.map((c) =>
                c.id === data.challenge_id
                  ? {
                      ...c,
                      status: 'COMPLETED',
                      progress: 100,
                      durationSeconds: data.duration_seconds ?? c.durationSeconds,
                      duration_seconds: data.duration_seconds ?? c.duration_seconds
                    }
                  : c
              )
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id
                ? {
                    ...prev,
                    status: 'COMPLETED',
                    progress: 100,
                    durationSeconds: data.duration_seconds ?? prev.durationSeconds,
                    duration_seconds: data.duration_seconds ?? prev.duration_seconds
                  }
                : prev
            );
          } else if (data.event === 'RUN_AWAITING_FLAG') {
            setChallenges((prev) =>
              prev.map((c) =>
                c.id === data.challenge_id
                  ? {
                      ...c,
                      status: 'AWAITING_FLAG',
                      durationSeconds: data.duration_seconds ?? c.durationSeconds,
                      duration_seconds: data.duration_seconds ?? c.duration_seconds
                    }
                  : c
              )
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id
                ? {
                    ...prev,
                    status: 'AWAITING_FLAG',
                    durationSeconds: data.duration_seconds ?? prev.durationSeconds,
                    duration_seconds: data.duration_seconds ?? prev.duration_seconds
                  }
                : prev
            );
          } else if (data.event === 'AGENT_UPDATE') {
            if (Array.isArray(data.agents) && data.agents.length > 0) {
              setAgents(mapSwarmAgents(data.agents));
            }
          } else if (data.event === 'RUN_STALLED' || data.event === 'RUN_FAILED') {
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, status: 'FAILED' } : c))
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id ? { ...prev, status: 'FAILED' } : prev
            );
          } else if (data.event === 'RUN_PAUSED' || data.event === 'CHALLENGE_PAUSED') {
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, status: 'PAUSED' } : c))
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id ? { ...prev, status: 'PAUSED' } : prev
            );
          } else if (data.event === 'CHECKPOINT_REACHED') {
            // Hard-pause HITL checkpoint: store the consolidated report + flip status.
            setCheckpointReports((prev) => ({
              ...prev,
              [data.challenge_id]: { cycle: data.cycle, report: data.report || '' }
            }));
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, status: 'WAITING_FOR_USER' } : c))
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id ? { ...prev, status: 'WAITING_FOR_USER' } : prev
            );
            try { soundEngine.playWarning(); } catch (e) {}
          } else if (data.event === 'CHECKPOINT_RESUMED' || data.event === 'CHECKPOINT_PARSE_ERROR') {
            setCheckpointReports((prev) => {
              const next = { ...prev };
              delete next[data.challenge_id];
              return next;
            });
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, status: 'RUNNING' } : c))
            );
            setActiveChallenge((prev) =>
              prev && prev.id === data.challenge_id ? { ...prev, status: 'RUNNING' } : prev
            );
          } else if (data.event === 'KILL_SWITCH_ACTIVATED') {
            setKillSwitchActive(true);
            setShowModalKillSwitch(true);
          } else if (data.event === 'PACKAGE_INSTALL_REQUEST') {
            setPackageRequests((prev) => [
              ...prev.filter((r) => r.requestId !== data.request_id),
              {
                requestId: data.request_id,
                challengeId: data.challenge_id,
                challengeName: data.challenge_name || 'Autonomous Target',
                packageName: data.package_name,
                importName: data.import_name,
                errorSnippet: data.error_snippet || '',
                timestamp: data.timestamp || new Date().toLocaleTimeString()
              }
            ]);
          } else if (data.event === 'PACKAGE_INSTALL_RESULT') {
            setPackageRequests((prev) => prev.filter((r) => r.requestId !== data.request_id));
          } else if (data.event === 'ROOT_PERMISSION_REQUEST') {
            setRootRequests((prev) => [
              ...prev.filter((r) => r.requestId !== data.request_id),
              {
                requestId: data.request_id,
                challengeId: data.challenge_id,
                challengeName: data.challenge_name || 'Autonomous Agent',
                command: data.command,
                reason: data.reason || 'Superuser privilege required',
                errorSnippet: data.error_snippet || '',
                timestamp: data.timestamp || new Date().toLocaleTimeString()
              }
            ]);
          } else if (data.event === 'ROOT_PERMISSION_RESULT') {
            setRootRequests((prev) => prev.filter((r) => r.requestId !== data.request_id));
          } else if (data.type === 'PROVIDER_FALLBACK_TRIGGERED' || data.event === 'PROVIDER_FALLBACK_TRIGGERED') {
            const fallbackData = data.data || data;
            setFallbackNotice({
              failedProvider: fallbackData.failed_provider || 'Provider',
              reason: fallbackData.reason || 'Quota Exceeded / Endpoint Error',
              nextProvider: fallbackData.next_provider || 'Auto-Fallback Candidate',
              timestamp: new Date().toLocaleTimeString()
            });
            try { soundEngine.playWarning(); } catch (e) {}
            setTimeout(() => setFallbackNotice(null), 8000);
          } else if (data.event === 'KNOWLEDGE_UPDATED' || data.event === 'KNOWLEDGE_BULK_INGESTED') {
            setKnowledgeRefreshTrigger((prev) => prev + 1);
          }
        } catch (err) {
          console.error('WS Parse Error', err);
        }
      };
    } catch (e) {
      console.log('WS Connection notice: local fallback mode active.');
    }

    return () => {
      if (ws) ws.close();
    };
  }, []);

  const fetchBackendData = async () => {
    try {
      const backendChallenges = await apiService.getChallenges();
      if (Array.isArray(backendChallenges)) {
        const formatted: Challenge[] = backendChallenges.map((c: any) => ({
          id: c.id,
          name: c.name,
          category: c.category,
          difficulty: c.difficulty || 'MEDIUM',
          target: c.target_address || c.target || '127.0.0.1',
          status: c.status,
          progress: c.progress || 0,
          lastActivity: 'Just now',
          flagStatus: c.flagStatus || c.flag_status || 'UNFOUND',
          flag: c.flag,
          description: c.description,
          workingDirectory: c.working_directory,
          platformName: c.platform_name,
          createdAt: c.created_at || c.createdAt,
          created_at: c.created_at || c.createdAt,
          startedAt: c.started_at || c.startedAt,
          started_at: c.started_at || c.startedAt,
          completedAt: c.completed_at || c.completedAt,
          completed_at: c.completed_at || c.completedAt,
          durationSeconds: c.duration_seconds || c.durationSeconds || 0,
          duration_seconds: c.duration_seconds || c.durationSeconds || 0,
          missionPlan: c.mission_plan || c.missionPlan,
          mission_plan: c.mission_plan || c.missionPlan
        }));
        setChallenges(formatted);
      }

      const backendTargets = await apiService.getTargets();
      if (Array.isArray(backendTargets)) {
        const formattedT: Target[] = backendTargets.map((t: any) => ({
          id: t.id,
          currentIp: t.current_address,
          hostname: t.hostname,
          services: [
            { port: 80, proto: 'tcp', service: 'HTTP', version: 'Target Server' }
          ],
          technologies: ['Linux'],
          status: t.verification_status.toUpperCase(),
          discoveryMethod: 'FORGE Auto Ingest',
          lastVerified: 'Just now',
          addressHistory: [t.current_address],
          challengeId: t.challenge_id
        }));
        setTargets(formattedT);
      }

      const backendEvidence = await apiService.getEvidence();
      if (Array.isArray(backendEvidence)) {
        setEvidenceList(backendEvidence);
      }

      const backendFindings = await apiService.getFindings();
      if (Array.isArray(backendFindings)) {
        setFindings(backendFindings);
      }

      const backendTools = await apiService.getTools();
      if (Array.isArray(backendTools) && backendTools.length > 0) {
        setTools(backendTools);
      }

      const backendProviders = await apiService.getProviders();
      const provList = Array.isArray(backendProviders)
        ? backendProviders
        : (backendProviders && Array.isArray((backendProviders as any).providers) ? (backendProviders as any).providers : []);
      if (provList.length > 0) {
        setProviders(provList.map((p: any) => ({
          name: p.name || 'Provider',
          status: p.status === 'HEALTHY' ? 'HEALTHY' : (p.status === 'DEGRADED' ? 'DEGRADED' : 'HEALTHY'),
          model: p.default_model || p.model || p.name || '',
          transport: p.transport === 'CLI' ? 'CLI' : 'API',
          latency: p.latency_ms ? `${p.latency_ms}ms` : '—',
          requests: 0,
          quota: p.quota || '—',
          lastError: 'None',
          fallbackPriority: 0
        })));
      }

      // Reload-safe terminal history (persisted tool executions from the swarm/orchestrator)
      const backendExecutions = await apiService.getToolExecutions();
      if (Array.isArray(backendExecutions) && backendExecutions.length > 0) {
        setTerminalLogs(mapToolExecutionsToLogs(backendExecutions));
      }

      // Live swarm worker fleet (fallback: keep the idle placeholder fleet)
      const backendAgents = await apiService.getAgents();
      if (Array.isArray(backendAgents) && backendAgents.length > 0) {
        setAgents(mapSwarmAgents(backendAgents));
      }
    } catch (e) {
      console.log('Backend sync active.');
    }
  };

  const handleCreateChallenge = async (newCh: {
    name: string;
    category: any;
    difficulty: any;
    target: string;
    description: string;
    workingDirectory?: string;
    platformName?: string;
    requiresRoot?: boolean;
    flagPattern?: string;
    maxIterations?: number;
    maxMinutes?: number;
    instanceExpiryMinutes?: number;
    attachedFilePaths?: string[];
  }) => {
    const createdLocally: Challenge = {
      id: `ch-${Date.now()}`,
      name: newCh.name,
      category: newCh.category,
      difficulty: newCh.difficulty,
      target: newCh.target,
      status: 'RUNNING',
      progress: 0,
      lastActivity: 'Just now',
      flagStatus: 'UNFOUND',
      description: newCh.description,
      workingDirectory: newCh.workingDirectory,
      platformName: newCh.platformName
    };
    setChallenges((prev) => [createdLocally, ...prev]);

    const newTargetObj: Target = {
      id: `TARGET-${Math.floor(1000 + Math.random() * 9000)}`,
      currentIp: newCh.target,
      hostname: `${newCh.name.toLowerCase()}.ctf`,
      services: [
        { port: 80, proto: 'tcp', service: 'HTTP', version: 'Target Server' }
      ],
      technologies: ['HTTP', 'Linux'],
      status: 'VERIFIED',
      discoveryMethod: 'FORGE Auto Ingest',
      lastVerified: 'Just now',
      addressHistory: [newCh.target],
      challengeId: createdLocally.id
    };
    setTargets((prev) => [newTargetObj, ...prev]);

    try {
      const resp = await apiService.createChallenge({
        name: newCh.name,
        category: newCh.category,
        difficulty: newCh.difficulty,
        description: newCh.description,
        target_address: newCh.target,
        working_directory: newCh.workingDirectory,
        platform_name: newCh.platformName,
        requires_root: newCh.requiresRoot,
        flag_pattern: newCh.flagPattern,
        max_iterations: newCh.maxIterations,
        max_minutes: newCh.maxMinutes,
        instance_expiry_minutes: newCh.instanceExpiryMinutes,
        attached_file_paths: newCh.attachedFilePaths
      });
      if (resp && resp.id) {
        setChallenges((prev) =>
          prev.map((c) => (c.id === createdLocally.id ? { ...c, id: resp.id } : c))
        );
        // Resync from the API: the mission plan can race past the optimistic-id swap,
        // so pull the freshly generated plan instead of relying on the WS event alone.
        fetchBackendData();
      }
    } catch (e) {
      console.warn('Backend API challenge creation offline fallback:', e);
    }
  };

  const handleToggleChallengeStatus = async (id: string) => {
    const current = challenges.find((c) => c.id === id);
    const isRunning = current?.status === 'RUNNING';
    const nextStatus: Challenge['status'] = isRunning ? 'PAUSED' : 'RUNNING';

    // Optimistic UI flip for both the list and the open workspace.
    setChallenges((prev) =>
      prev.map((c) => (c.id === id ? { ...c, status: nextStatus } : c))
    );
    setActiveChallenge((prev) => (prev && prev.id === id ? { ...prev, status: nextStatus } : prev));

    try {
      if (isRunning) {
        // Pause: gracefully suspends the live swarm and saves a resume snapshot.
        await apiService.pauseChallenge(id);
      } else {
        // Start/Resume: backend decides fresh vs resume from persisted progress.
        await apiService.startRun(id);
      }
    } catch (e) {
      console.warn('Challenge start/pause backend call failed:', e);
    }
  };

  const handleDeleteChallenge = async (id: string) => {
    setChallenges((prev) => prev.filter((c) => c.id !== id));
    setTargets((prev) => prev.filter((t) => t.challengeId !== id));
    if (activeChallenge?.id === id) {
      setActiveChallenge(null);
    }
    try {
      await apiService.deleteChallenge(id);
    } catch (e) {
      console.warn('Delete challenge fallback:', e);
    }
  };

  const handleDeleteAllChallenges = async () => {
    setChallenges([]);
    setTargets([]);
    setActiveChallenge(null);
    try {
      await apiService.deleteAllChallenges();
    } catch (e) {
      console.warn('Delete all challenges fallback:', e);
    }
  };

  const handleTriggerKillSwitch = async () => {
    setKillSwitchActive(true);
    setShowModalKillSwitch(true);
    try {
      await fetch('/api/killswitch', { method: 'POST' });
    } catch (e) {
      console.log('Kill switch triggered locally');
    }
  };

  const handleResumeKillSwitch = () => {
    setKillSwitchActive(false);
    setShowModalKillSwitch(false);
  };

  const handleMinimizeKillSwitchModal = () => {
    setShowModalKillSwitch(false);
  };

  const handleApprovePackageInstall = async (requestId: string, packageName: string) => {
    try {
      await apiService.installPackage(requestId, packageName);
    } catch (e) {
      console.error('Failed to approve package install:', e);
    }
  };

  const handleDismissPackageInstall = async (requestId: string) => {
    setPackageRequests((prev) => prev.filter((r) => r.requestId !== requestId));
    try {
      await apiService.skipPackageInstall(requestId);
    } catch (e) {
      console.error('Failed to skip package install:', e);
    }
  };

  const handleApproveRoot = async (requestId: string, command: string, sudoPassword?: string) => {
    try {
      await apiService.approvePrivilege(
        requestId,
        command,
        sudoPassword,
        activeChallenge?.id,
        activeChallenge?.workingDirectory
      );
    } catch (e) {
      console.error('Failed to approve root permission:', e);
    }
  };

  const handleDismissRoot = async (requestId: string) => {
    setRootRequests((prev) => prev.filter((r) => r.requestId !== requestId));
    try {
      await apiService.rejectPrivilege(requestId, activeChallenge?.id);
    } catch (e) {
      console.error('Failed to reject root permission:', e);
    }
  };

  const handleOpenChallengeWorkspace = (ch: Challenge) => {
    setActiveChallenge(ch);
  };

  const handleClearActiveChallenge = () => {
    setActiveChallenge(null);
  };

  // Newest RUNNING challenge first (avoids the dashboard locking onto the oldest zombie run);
  // when nothing is RUNNING, fall back to the most recently started challenge.
  const startTime = (c: Challenge) => {
    const s = c.started_at || c.created_at;
    return s ? parseUtcMs(s) ?? 0 : 0;
  };
  const currentActiveChallenge: Challenge | undefined =
    [...challenges]
      .filter((c) => c.status === 'RUNNING')
      .sort((a, b) => startTime(b) - startTime(a))[0] ||
    [...challenges].sort((a, b) => startTime(b) - startTime(a))[0];

  const currentTarget: Target = (activeChallenge ? targets.find((t) => t.challengeId === activeChallenge.id) : undefined) || targets[0] || {
    id: activeChallenge?.id || 'target-main',
    currentIp: activeChallenge?.target || '127.0.0.1',
    hostname: `${(activeChallenge?.name || 'target').toLowerCase().replace(/\s+/g, '_')}.ctf`,
    services: [
      { port: 80, proto: 'tcp', service: 'HTTP', version: 'Target Server' }
    ],
    technologies: ['Linux'],
    status: 'VERIFIED',
    discoveryMethod: 'FORGE Auto Ingest',
    lastVerified: 'Just now',
    addressHistory: [activeChallenge?.target || '127.0.0.1'],
    challengeId: activeChallenge?.id
  };

  return (
    <div className="h-screen w-screen flex bg-[#06090e] text-slate-100 overflow-hidden font-sans select-none">
      {/* 1. Global Left Sidebar Shell */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        activeChallengeId={activeChallenge?.id || null}
        onClearActiveChallenge={handleClearActiveChallenge}
        onTriggerKillSwitch={handleTriggerKillSwitch}
        killSwitchActive={killSwitchActive}
      />

      {/* Main Content Column */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* 2. Global Top Bar Shell */}
        <TopBar
          activeTab={activeTab}
          activeChallenge={activeChallenge}
          killSwitchActive={killSwitchActive}
          operationalMode={operationalMode}
          onModeChange={setOperationalMode}
          onResumeKillSwitch={handleResumeKillSwitch}
        />

        {/* 3. Page Router Body */}
        <main className="flex-1 overflow-y-auto p-5 bg-[#06090e]">
          {/* LIVE PROVIDER FALLBACK NOTIFICATION BANNER */}
          {fallbackNotice && (
            <div className="mb-4 p-3.5 bg-obsidian-950/95 border-2 border-cyber-amber text-slate-100 rounded-xl text-xs flex items-center justify-between shadow-[0_0_25px_rgba(245,158,11,0.35)] animate-pulse font-mono">
              <div className="flex items-center space-x-3">
                <AlertTriangle className="w-5 h-5 text-cyber-amber flex-shrink-0" />
                <div>
                  <div className="flex items-center space-x-2">
                    <span className="font-bold text-cyber-amber uppercase tracking-wider">
                      PROVIDER NOTICE: [{fallbackNotice.failedProvider}]
                    </span>
                    <span className="text-[10px] text-slate-400">({fallbackNotice.timestamp})</span>
                  </div>
                  <p className="text-[11px] text-slate-300 mt-0.5">
                    {fallbackNotice.reason} • <strong className="text-cyber-emerald">Auto-cascading to {fallbackNotice.nextProvider}</strong>
                  </p>
                </div>
              </div>
              <button 
                onClick={() => setFallbackNotice(null)} 
                className="text-slate-400 hover:text-slate-100 p-1 rounded hover:bg-slate-800"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* IF DEDICATED CHALLENGE WORKSPACE IS ACTIVE */}
          {activeChallenge ? (
            <ChallengeWorkspace
              challenge={challenges.find((c) => c.id === activeChallenge.id) || activeChallenge}
              target={currentTarget}
              agents={agents.filter((a) => !a.challengeId || a.challengeId === activeChallenge.id)}
              evidenceList={evidenceList.filter((e) => !e.challengeId || e.challengeId === activeChallenge.id)}
              decisions={decisions.filter((d) => !d.challengeId || d.challengeId === activeChallenge.id)}
              logs={terminalLogs.filter((l) => !l.challengeId || l.challengeId === activeChallenge.id)}
              findings={findings.filter((f) => !f.challengeId || f.challengeId === activeChallenge.id)}
              workflowNodes={workflowNodes}
              checkpoint={checkpointReports[activeChallenge.id]}
              onSubmitCheckpoint={(text: string) => apiService.respondToCheckpoint(activeChallenge.id, text)}
              onBackToChallenges={handleClearActiveChallenge}
              onToggleStatus={handleToggleChallengeStatus}
            />
          ) : (
            /* PRIMARY 10 PAGES */
            <>
              {activeTab === 'command' && (
                <CommandCenter
                  activeChallenge={currentActiveChallenge}
                  target={targets.find((t) => t.challengeId === currentActiveChallenge?.id) || targets[0]}
                  agents={agents}
                  providers={providers}
                  logs={terminalLogs}
                  onOpenWorkspace={handleOpenChallengeWorkspace}
                  onTriggerKillSwitch={handleTriggerKillSwitch}
                  killSwitchActive={killSwitchActive}
                />
              )}

              {activeTab === 'challenges' && (
                <Challenges
                  challenges={challenges}
                  onSelectChallenge={handleOpenChallengeWorkspace}
                  onCreateChallenge={handleCreateChallenge}
                  onToggleStatus={handleToggleChallengeStatus}
                  onDeleteChallenge={handleDeleteChallenge}
                  onDeleteAllChallenges={handleDeleteAllChallenges}
                />
              )}

              {activeTab === 'targets' && (
                <Targets
                  targets={targets}
                  onRediscover={(id) => apiService.rediscoverTarget(id)}
                  onVerify={(id) => apiService.verifyTarget(id)}
                  onCreateTarget={handleCreateChallenge}
                  onNavigateTab={(tab) => setActiveTab(tab)}
                />
              )}

              {activeTab === 'agents' && (
                <Agents agents={agents} />
              )}

              {activeTab === 'tools' && (
                <Tools tools={tools} />
              )}

              {activeTab === 'ai_intelligence' && (
                <AiIntelligence decisions={decisions} routes={routes} />
              )}

              {activeTab === 'playbooks' && (
                <PlaybooksVault />
              )}

              {activeTab === 'knowledge' && (
                <KnowledgeCoverage refreshTrigger={knowledgeRefreshTrigger} />
              )}

              {activeTab === 'evidence' && (
                <Evidence evidence={evidenceList} />
              )}

              {activeTab === 'terminal' && (
                <TerminalView logs={terminalLogs} activeChallengeId={undefined} />
              )}

              {activeTab === 'providers' && (
                <Providers
                  providers={providers}
                  onTestConnection={(name) => console.log('Testing connection:', name)}
                />
              )}

              {activeTab === 'system' && (
                <SystemView
                  checkpoints={checkpoints}
                  auditLogs={auditLogs}
                  onResumeCheckpoint={(runId) => console.log('Resuming run:', runId)}
                />
              )}
            </>
          )}
        </main>
      </div>

      {/* 4. Global Emergency Stop Overlay Modal */}
      <EmergencyStopModal
        isOpen={showModalKillSwitch}
        onResume={handleResumeKillSwitch}
        onMinimize={handleMinimizeKillSwitchModal}
      />

      {/* 5. Missing Package Install Approval Modal */}
      <PackageInstallModal
        requests={packageRequests}
        onApprove={handleApprovePackageInstall}
        onDismiss={handleDismissPackageInstall}
      />

      {/* 6. Root / Privileged Elevation Approval Modal */}
      <RootPermissionModal
        requests={rootRequests}
        onApprove={handleApproveRoot}
        onDismiss={handleDismissRoot}
      />
    </div>
  );
}

