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
} from './data/mockData';

import { Sidebar } from './components/Shell/Sidebar';
import { TopBar } from './components/Shell/TopBar';
import { EmergencyStopModal } from './components/Shell/EmergencyStopModal';

import { CommandCenter } from './components/Pages/CommandCenter';
import { Challenges } from './components/Pages/Challenges';
import { Targets } from './components/Pages/Targets';
import { Agents } from './components/Pages/Agents';
import { Tools } from './components/Pages/Tools';
import { AiIntelligence } from './components/Pages/AiIntelligence';
import { Evidence } from './components/Pages/Evidence';
import { TerminalView } from './components/Pages/TerminalView';
import { Providers } from './components/Pages/Providers';
import { SystemView } from './components/Pages/SystemView';
import { ChallengeWorkspace } from './components/Pages/ChallengeWorkspace';
import { apiService } from './services/api';

export default function App() {
  const [activeTab, setActiveTab] = useState<NavTab>('command');
  const [activeChallenge, setActiveChallenge] = useState<Challenge | null>(null);
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [showModalKillSwitch, setShowModalKillSwitch] = useState(false);
  const [operationalMode, setOperationalMode] = useState<string>('CTF_OFFENSIVE_CONTROLLED');

  // Application Data States
  const [challenges, setChallenges] = useState<Challenge[]>(INITIAL_CHALLENGES);
  const [targets, setTargets] = useState<Target[]>(INITIAL_TARGETS);
  const [agents] = useState<AgentInfo[]>(INITIAL_AGENTS);
  const [tools] = useState<ToolItem[]>(INITIAL_TOOLS);
  const [decisions, setDecisions] = useState<AiDecision[]>(INITIAL_AI_DECISIONS);
  const [routes] = useState<ModelRoute[]>(INITIAL_MODEL_ROUTES);
  const [evidenceList, setEvidenceList] = useState<EvidenceItem[]>(INITIAL_EVIDENCE);
  const [terminalLogs, setTerminalLogs] = useState<TerminalLog[]>(INITIAL_TERMINAL_LOGS);
  const [providers] = useState<ProviderInfo[]>(INITIAL_PROVIDERS);
  const [checkpoints] = useState<CheckpointItem[]>(INITIAL_CHECKPOINTS);
  const [auditLogs] = useState<AuditLog[]>(INITIAL_AUDIT_LOGS);
  const [findings, setFindings] = useState<Finding[]>(INITIAL_FINDINGS);
  const [workflowNodes] = useState<WorkflowNode[]>(INITIAL_WORKFLOW_PIPELINE);

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
          } else if (data.event === 'PROGRESS_UPDATED') {
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, progress: data.progress, status: 'RUNNING' } : c))
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
              prev.map((c) => (c.id === data.challenge_id ? { ...c, flagStatus: 'CAPTURED', flag: data.flag } : c))
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
              prev.map((c) => (c.id === data.challenge_id ? { ...c, status: 'COMPLETED', progress: 100 } : c))
            );
          } else if (data.event === 'KILL_SWITCH_ACTIVATED') {
            setKillSwitchActive(true);
            setShowModalKillSwitch(true);
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
      if (Array.isArray(backendChallenges) && backendChallenges.length > 0) {
        const formatted: Challenge[] = backendChallenges.map((c: any) => ({
          id: c.id,
          name: c.name,
          category: c.category,
          difficulty: c.difficulty || 'MEDIUM',
          target: c.target_address || c.target || '127.0.0.1',
          status: c.status,
          progress: c.progress || 0,
          lastActivity: 'Just now',
          flagStatus: c.flagStatus || 'UNFOUND',
          flag: c.flag,
          description: c.description,
          workingDirectory: c.working_directory,
          platformName: c.platform_name
        }));
        setChallenges(formatted);
      }

      const backendTargets = await apiService.getTargets();
      if (Array.isArray(backendTargets) && backendTargets.length > 0) {
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
    } catch (e) {
      console.log('Backend sync: using initial state');
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
        platform_name: newCh.platformName
      });
      if (resp && resp.id) {
        setChallenges((prev) =>
          prev.map((c) => (c.id === createdLocally.id ? { ...c, id: resp.id } : c))
        );
      }
    } catch (e) {
      console.warn('Backend API challenge creation offline fallback:', e);
    }
  };

  const handleToggleChallengeStatus = (id: string) => {
    setChallenges((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, status: c.status === 'RUNNING' ? 'PAUSED' : 'RUNNING' } : c
      )
    );
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

  const handleOpenChallengeWorkspace = (ch: Challenge) => {
    setActiveChallenge(ch);
  };

  const handleClearActiveChallenge = () => {
    setActiveChallenge(null);
  };

  const currentTarget = targets.find((t) => t.challengeId === activeChallenge?.id) || targets[0];

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
          {/* IF DEDICATED CHALLENGE WORKSPACE IS ACTIVE */}
          {activeChallenge ? (
            <ChallengeWorkspace
              challenge={activeChallenge}
              target={currentTarget}
              evidenceList={evidenceList.filter((e) => !e.challengeId || e.challengeId === activeChallenge.id)}
              decisions={decisions.filter((d) => !d.challengeId || d.challengeId === activeChallenge.id)}
              logs={terminalLogs.filter((l) => !l.challengeId || l.challengeId === activeChallenge.id)}
              findings={findings.filter((f) => !f.challengeId || f.challengeId === activeChallenge.id)}
              workflowNodes={workflowNodes}
              onBackToChallenges={handleClearActiveChallenge}
              onToggleStatus={handleToggleChallengeStatus}
            />
          ) : (
            /* PRIMARY 10 PAGES */
            <>
              {activeTab === 'command' && (
                <CommandCenter
                  activeChallenge={challenges[0]}
                  target={targets[0]}
                  agents={agents}
                  providers={providers}
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
    </div>
  );
}

