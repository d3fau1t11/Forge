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

export default function App() {
  const [activeTab, setActiveTab] = useState<NavTab>('command');
  const [activeChallenge, setActiveChallenge] = useState<Challenge | null>(null);
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [operationalMode, setOperationalMode] = useState<string>('CTF_OFFENSIVE_CONTROLLED');

  // Application Data States
  const [challenges, setChallenges] = useState<Challenge[]>(INITIAL_CHALLENGES);
  const [targets, setTargets] = useState<Target[]>(INITIAL_TARGETS);
  const [agents] = useState<AgentInfo[]>(INITIAL_AGENTS);
  const [tools] = useState<ToolItem[]>(INITIAL_TOOLS);
  const [decisions] = useState<AiDecision[]>(INITIAL_AI_DECISIONS);
  const [routes] = useState<ModelRoute[]>(INITIAL_MODEL_ROUTES);
  const [evidenceList] = useState<EvidenceItem[]>(INITIAL_EVIDENCE);
  const [terminalLogs] = useState<TerminalLog[]>(INITIAL_TERMINAL_LOGS);
  const [providers] = useState<ProviderInfo[]>(INITIAL_PROVIDERS);
  const [checkpoints] = useState<CheckpointItem[]>(INITIAL_CHECKPOINTS);
  const [auditLogs] = useState<AuditLog[]>(INITIAL_AUDIT_LOGS);
  const [findings] = useState<Finding[]>(INITIAL_FINDINGS);
  const [workflowNodes] = useState<WorkflowNode[]>(INITIAL_WORKFLOW_PIPELINE);

  useEffect(() => {
    // Fetch live data from FastAPI backend where available
    fetchBackendData();

    // Setup live WebSocket listener
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`ws://${window.location.host}/ws/events`);
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === 'RUN_STARTED') {
            setChallenges((prev) =>
              prev.map((c) => (c.id === data.challenge_id ? { ...c, status: 'RUNNING' } : c))
            );
          } else if (data.event === 'KILL_SWITCH_ACTIVATED') {
            setKillSwitchActive(true);
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
      const resHealth = await fetch('/api/health');
      if (resHealth.ok) {
        const data = await resHealth.json();
        console.log('Backend health status:', data);
      }
    } catch (e) {
      console.log('Backend sync: using local state');
    }
  };

  const handleCreateChallenge = (newCh: {
    name: string;
    category: any;
    difficulty: any;
    target: string;
    description: string;
  }) => {
    const created: Challenge = {
      id: `ch-${Date.now()}`,
      name: newCh.name,
      category: newCh.category,
      difficulty: newCh.difficulty,
      target: newCh.target,
      status: 'RUNNING',
      progress: 0,
      lastActivity: 'Just now',
      flagStatus: 'UNFOUND',
      description: newCh.description
    };
    setChallenges((prev) => [created, ...prev]);

    // Also register target identity
    const newTargetObj: Target = {
      id: `TARGET-${Math.floor(1000 + Math.random() * 9000)}`,
      currentIp: newCh.target,
      hostname: `${newCh.name.toLowerCase()}.ctf`,
      services: [
        { port: 80, proto: 'tcp', service: 'HTTP', version: 'Target Web Server' }
      ],
      technologies: ['HTTP', 'Linux'],
      status: 'VERIFIED',
      discoveryMethod: 'FORGE Auto Ingest',
      lastVerified: 'Just now',
      addressHistory: [newCh.target],
      challengeId: created.id
    };
    setTargets((prev) => [newTargetObj, ...prev]);
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
    try {
      await fetch('/api/killswitch', { method: 'POST' });
    } catch (e) {
      console.log('Kill switch triggered locally');
    }
  };

  const handleResumeKillSwitch = () => {
    setKillSwitchActive(false);
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
                  onRediscover={(id) => console.log('Rediscovering target:', id)}
                  onVerify={(id) => console.log('Verifying target:', id)}
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
                <TerminalView logs={terminalLogs} />
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
        isOpen={killSwitchActive}
        onResume={handleResumeKillSwitch}
      />
    </div>
  );
}
