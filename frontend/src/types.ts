export type NavTab = 
  | 'command' 
  | 'challenges' 
  | 'targets' 
  | 'agents' 
  | 'tools' 
  | 'ai_intelligence'
  | 'playbooks'
  | 'knowledge'
  | 'memory'
  | 'evidence'
  | 'terminal' 
  | 'providers' 
  | 'system';

export type ChallengeTab = 
  | 'overview' 
  | 'todo_plan'
  | 'workflow' 
  | 'terminal' 
  | 'ai_decisions' 
  | 'evidence' 
  | 'findings' 
  | 'readme';

export interface MissionTask {
  id: string;
  phase: 'RECON' | 'SURFACE_ANALYSIS' | 'EXPLOITATION' | 'FLAG_EXTRACTION' | 'VERIFICATION';
  title: string;
  tool: string;
  reasoning: string;
  playbook_ref?: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED' | 'REVISED';
  output_summary?: string;
}

export interface StrategicReview {
  id: string;
  timestamp: string;
  reviewer_model: string;
  stuck_reason: string;
  diagnosis: string;
  pivot_strategy: string;
}

export interface MissionPlan {
  challenge_id: string;
  status: string;
  summary: string;
  model?: string;
  created_at: string;
  updated_at: string;
  tasks: MissionTask[];
  strategic_reviews?: StrategicReview[];
}

export interface Challenge {
  id: string;
  name: string;
  category: string;
  difficulty: 'EASY' | 'MEDIUM' | 'HARD' | 'INSANE';
  target: string;
  status: 'RUNNING' | 'PAUSED' | 'WAITING' | 'WAITING_FOR_USER' | 'SOLVED' | 'FAILED' | 'COMPLETED' | 'QUEUED' | 'AWAITING_FLAG';
  progress: number; // 0 - 100
  lastActivity: string;
  flagStatus: 'UNFOUND' | 'CAPTURED' | 'VERIFYING';
  flag?: string;
  description?: string;
  workingDirectory?: string;
  platformName?: string;
  createdAt?: string;
  created_at?: string;
  startedAt?: string;
  started_at?: string;
  completedAt?: string;
  completed_at?: string;
  durationSeconds?: number;
  duration_seconds?: number;
  missionPlan?: MissionPlan;
  mission_plan?: MissionPlan;
  candidates?: FlagCandidate[];
  derivedArtifacts?: DerivedArtifact[];
  decisions?: AiDecision[];
}

export interface Target {
  id: string;
  currentIp: string;
  hostname: string;
  services: { port: number; proto: string; service: string; version?: string }[];
  technologies: string[];
  status: string;
  discoveryMethod: string;
  lastVerified: string;
  addressHistory: string[];
  challengeId?: string;
}

export interface AgentInfo {
  id: string;
  name: 'ORCHESTRATOR' | 'RECON' | 'WEB' | 'FORENSICS' | 'CRYPTO' | 'PWN' | 'REV';
  status: 'RUNNING' | 'ANALYZING' | 'IDLE' | 'STANDBY' | 'FAILED';
  currentObjective: string;
  currentCapability: string;
  selectedModel: string;
  lastTool: string;
  lastResult: string;
  runtime: string;
  actionsCompleted: number;
  failures: number;
  checkpointStatus: string;
  challengeId?: string;
}

export interface ToolItem {
  name: string;
  capabilityCategory: string;
  binary: string;
  installed: boolean;
  version: string;
  status: 'READY' | 'EXECUTING' | 'DEGRADED';
  executionCount: number;
  lastExecution: string;
  fallbackTool: string;
}

export interface AiDecision {
  id: string;
  timestamp: string;
  agent: string;
  goal: string;
  capability: string;
  selectedTool: string;
  reason?: string;
  result: string;
  nextAction?: string;
  confidence: number;
  costUsd?: number;
  model?: string;
  challengeId?: string;
}

export interface ModelRoute {
  task: string;
  selectedProvider: string;
  model: string;
  reason: string;
  latency: string;
  fallback: string;
}

export interface EvidenceItem {
  id: string;
  timestamp: string;
  source: string;
  agent: string;
  tool?: string;
  target?: string;
  type: string;
  description: string;
  content: string;
  hash?: string;
  challengeId?: string;
}

export interface TerminalLog {
  id: string;
  timestamp: string;
  type: 'FORGE TOOL EXECUTION' | 'CLAUDE CODE' | 'CODEX' | 'SYSTEM' | 'EXECUTION';
  command: string;
  output: string;
  exitCode: number;
  duration: string;
  privilege?: 'SAFE' | 'ELEVATED' | 'SUDO';
  agent?: string;
  challengeId?: string;
}

export interface ProviderInfo {
  name: string;
  status: 'HEALTHY' | 'DEGRADED' | 'OFFLINE' | 'QUOTA_EXHAUSTED';
  model: string;
  transport: 'API' | 'CLI' | 'LOCAL';
  latency: string;
  requests: number;
  quota: string;
  lastError: string;
  fallbackPriority: number;
  routerNote?: string;
  quotaLimited?: boolean;         // True if model is subject to AgentRouter batch quota
  quotaExhausted?: boolean;       // True if current batch quota is exhausted (402)
  quotaFallbackModel?: string;    // Always-available fallback model (e.g. deepseek-v4-flash)
  nextBatchTime?: string;         // Next quota batch replenishment time
}

export interface CheckpointItem {
  runId: string;
  challenge: string;
  checkpointName: string;
  status: 'COMPLETED' | 'PAUSED' | 'ACTIVE';
  created: string;
  reason: string;
  completedSteps: string[];
  pendingSteps: string[];
}

export interface AuditLog {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  target: string;
  permission: 'SAFE' | 'PRIVILEGED';
  result: 'SUCCESS' | 'FAILED';
}

export interface Finding {
  id: string;
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO';
  title: string;
  target?: string;
  endpoint: string;
  status: 'VERIFIED' | 'UNVERIFIED' | 'EXPLOITED';
  evidenceRef?: string;
  description: string;
  challengeId?: string;
}

export interface WorkflowNode {
  id: string;
  label: string;
  status: 'COMPLETED' | 'ACTIVE' | 'PENDING' | 'FAILED';
  description: string;
  evidenceId?: string;
}

export interface FlagCandidate {
  flag: string;
  worker?: string;
  source?: string;
  timestamp?: string;
  verified?: boolean;
}

export interface DerivedArtifact {
  path?: string;
  filename?: string;
  original_path?: string;
  artifact_type?: string;
  encoding?: string;
  size_bytes?: number;
  mime_type?: string;
  status?: string;
  preview?: string;
  worker_id?: string;
  timestamp?: string;
}

