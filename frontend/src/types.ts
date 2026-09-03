export type NavTab = 
  | 'command' 
  | 'challenges' 
  | 'targets' 
  | 'agents' 
  | 'tools' 
  | 'ai_intelligence' 
  | 'evidence' 
  | 'terminal' 
  | 'providers' 
  | 'system';

export type ChallengeTab = 
  | 'overview' 
  | 'workflow' 
  | 'terminal' 
  | 'ai_decisions' 
  | 'evidence' 
  | 'findings' 
  | 'readme';

export interface Challenge {
  id: string;
  name: string;
  category: string;
  difficulty: 'EASY' | 'MEDIUM' | 'HARD' | 'INSANE';
  target: string;
  status: 'RUNNING' | 'PAUSED' | 'WAITING' | 'SOLVED' | 'FAILED';
  progress: number; // 0 - 100
  lastActivity: string;
  flagStatus: 'UNFOUND' | 'CAPTURED' | 'VERIFYING';
  flag?: string;
  description?: string;
}

export interface Target {
  id: string;
  currentIp: string;
  hostname: string;
  services: { port: number; proto: string; service: string; version?: string }[];
  technologies: string[];
  status: 'VERIFIED' | 'UNVERIFIED' | 'UNREACHABLE';
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
  reason: string;
  result: string;
  nextAction: string;
  confidence: number;
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
  tool: string;
  target: string;
  type: 'HTTP RESPONSE' | 'SCREENSHOT' | 'COMMAND OUTPUT' | 'FILE' | 'HASH' | 'BANNER' | 'FINDING' | 'FLAG';
  description: string;
  content: string;
  challengeId?: string;
}

export interface TerminalLog {
  id: string;
  timestamp: string;
  type: 'FORGE TOOL EXECUTION' | 'CLAUDE CODE' | 'CODEX' | 'SYSTEM';
  command: string;
  output: string;
  exitCode: number;
  duration: string;
  privilege: 'SAFE' | 'ELEVATED' | 'SUDO';
  agent?: string;
  challengeId?: string;
}

export interface ProviderInfo {
  name: string;
  status: 'HEALTHY' | 'DEGRADED' | 'OFFLINE';
  model: string;
  transport: 'API' | 'CLI' | 'LOCAL';
  latency: string;
  requests: number;
  quota: string;
  lastError: string;
  fallbackPriority: number;
  routerNote?: string;
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
  target: string;
  endpoint: string;
  status: 'VERIFIED' | 'UNVERIFIED' | 'EXPLOITED';
  evidenceRef: string;
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
