import { 
  Challenge, 
  Target, 
  AgentInfo, 
  ToolItem, 
  AiDecision, 
  ModelRoute, 
  EvidenceItem, 
  TerminalLog, 
  ProviderInfo, 
  CheckpointItem, 
  AuditLog, 
  Finding,
  WorkflowNode 
} from '../types';

export const INITIAL_CHALLENGES: Challenge[] = [];

export const INITIAL_TARGETS: Target[] = [];

export const INITIAL_AGENTS: AgentInfo[] = [
  {
    id: 'ag-orchestrator',
    name: 'ORCHESTRATOR',
    status: 'IDLE',
    currentObjective: 'Awaiting challenge initialization',
    currentCapability: 'pipeline_management',
    selectedModel: 'Gemini 1.5 Pro',
    lastTool: 'workflow_engine',
    lastResult: 'System online & ready',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'HEALTHY'
  },
  {
    id: 'ag-recon',
    name: 'RECON',
    status: 'IDLE',
    currentObjective: 'Standby for target scanning',
    currentCapability: 'network_scan',
    selectedModel: 'Cerebras (Fast Inference)',
    lastTool: 'nmap',
    lastResult: 'Ready for target IP',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'IDLE'
  },
  {
    id: 'ag-web',
    name: 'WEB',
    status: 'IDLE',
    currentObjective: 'Standby for HTTP surface fuzzing',
    currentCapability: 'directory_enumeration',
    selectedModel: 'Gemini 1.5 Pro',
    lastTool: 'ffuf',
    lastResult: 'Ready for target web URL',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'IDLE'
  },
  {
    id: 'ag-forensics',
    name: 'FORENSICS',
    status: 'IDLE',
    currentObjective: 'Standby for PCAP/file artifact analysis',
    currentCapability: 'packet_analysis',
    selectedModel: 'NVIDIA Code Model',
    lastTool: 'tshark',
    lastResult: 'No files queued',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'IDLE'
  },
  {
    id: 'ag-crypto',
    name: 'CRYPTO',
    status: 'STANDBY',
    currentObjective: 'Ciphertext & mathematical analysis standby',
    currentCapability: 'math_reasoning',
    selectedModel: 'Cerebras',
    lastTool: 'sage',
    lastResult: 'Standby for cryptographic payloads',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'STANDBY'
  },
  {
    id: 'ag-pwn',
    name: 'PWN',
    status: 'STANDBY',
    currentObjective: 'Binary exploitation payload crafting standby',
    currentCapability: 'binary_exploitation',
    selectedModel: 'AgentRouter / Claude Code',
    lastTool: 'pwntools',
    lastResult: 'Standby for binary ingest',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'STANDBY'
  },
  {
    id: 'ag-rev',
    name: 'REV',
    status: 'STANDBY',
    currentObjective: 'Decompilation & control-flow analysis standby',
    currentCapability: 'reverse_engineering',
    selectedModel: 'Codex / AgentRouter',
    lastTool: 'ghidra_headless',
    lastResult: 'Standby for binary ingest',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'STANDBY'
  }
];

export const INITIAL_TOOLS: ToolItem[] = [
  {
    name: 'nmap',
    capabilityCategory: 'NETWORK SCANNING',
    binary: '/usr/bin/nmap',
    installed: true,
    version: '7.94-RELEASE',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'rustscan'
  },
  {
    name: 'rustscan',
    capabilityCategory: 'NETWORK SCANNING',
    binary: '/usr/local/bin/rustscan',
    installed: true,
    version: '2.1.1',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'masscan'
  },
  {
    name: 'masscan',
    capabilityCategory: 'NETWORK SCANNING',
    binary: '/usr/bin/masscan',
    installed: true,
    version: '1.3.2',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'nmap'
  },
  {
    name: 'ffuf',
    capabilityCategory: 'DIRECTORY ENUMERATION',
    binary: '/usr/bin/ffuf',
    installed: true,
    version: 'v2.0.0-dev',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'gobuster'
  },
  {
    name: 'gobuster',
    capabilityCategory: 'DIRECTORY ENUMERATION',
    binary: '/usr/bin/gobuster',
    installed: true,
    version: 'v3.6.0',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'feroxbuster'
  },
  {
    name: 'feroxbuster',
    capabilityCategory: 'DIRECTORY ENUMERATION',
    binary: '/usr/local/bin/feroxbuster',
    installed: true,
    version: '2.10.1',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'ffuf'
  },
  {
    name: 'curl',
    capabilityCategory: 'WEB ANALYSIS',
    binary: '/usr/bin/curl',
    installed: true,
    version: '8.5.0',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'httpx'
  },
  {
    name: 'httpx',
    capabilityCategory: 'WEB ANALYSIS',
    binary: '/usr/local/bin/httpx',
    installed: true,
    version: 'v1.3.7',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'curl'
  },
  {
    name: 'browser (playwright)',
    capabilityCategory: 'WEB AUTOMATION',
    binary: 'playwright/chromium',
    installed: true,
    version: '1.40.0',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'curl'
  },
  {
    name: 'sqlmap',
    capabilityCategory: 'EXPLOITATION',
    binary: '/usr/bin/sqlmap',
    installed: true,
    version: '1.7.11#stable',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'curl'
  },
  {
    name: 'pwntools',
    capabilityCategory: 'BINARY EXPLOITATION',
    binary: 'python3 -m pwn',
    installed: true,
    version: '4.12.0',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'gdb_pwndbg'
  },
  {
    name: 'gdb_pwndbg',
    capabilityCategory: 'BINARY EXPLOITATION',
    binary: '/usr/bin/gdb',
    installed: true,
    version: '13.2 (pwndbg 2024.02)',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'ghidra_headless'
  },
  {
    name: 'ghidra_headless',
    capabilityCategory: 'REVERSE ENGINEERING',
    binary: '/opt/ghidra/support/analyzeHeadless',
    installed: true,
    version: '10.4',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'radare2'
  },
  {
    name: 'radare2',
    capabilityCategory: 'REVERSE ENGINEERING',
    binary: '/usr/bin/r2',
    installed: true,
    version: '5.8.8',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'ghidra_headless'
  },
  {
    name: 'binwalk',
    capabilityCategory: 'FILE ANALYSIS',
    binary: '/usr/bin/binwalk',
    installed: true,
    version: 'v2.3.3',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'file'
  },
  {
    name: 'tshark',
    capabilityCategory: 'PACKET ANALYSIS',
    binary: '/usr/bin/tshark',
    installed: true,
    version: '4.0.10',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'tcpdump'
  },
  {
    name: 'tcpdump',
    capabilityCategory: 'PACKET ANALYSIS',
    binary: '/usr/sbin/tcpdump',
    installed: true,
    version: '4.99.4',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'tshark'
  },
  {
    name: 'john',
    capabilityCategory: 'PASSWORD CRACKING',
    binary: '/usr/bin/john',
    installed: true,
    version: '1.9.0-jumbo-1',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'hashcat'
  },
  {
    name: 'hashcat',
    capabilityCategory: 'PASSWORD CRACKING',
    binary: '/usr/bin/hashcat',
    installed: true,
    version: 'v6.2.6',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'john'
  },
  {
    name: 'hydra',
    capabilityCategory: 'BRUTE FORCE',
    binary: '/usr/bin/hydra',
    installed: true,
    version: 'v9.5',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'patator'
  },
  {
    name: 'volatility3',
    capabilityCategory: 'MEMORY FORENSICS',
    binary: 'python3 -m volatility3',
    installed: true,
    version: '2.5.2',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'binwalk'
  },
  {
    name: 'searchsploit',
    capabilityCategory: 'VULNERABILITY INTELLIGENCE',
    binary: '/usr/bin/searchsploit',
    installed: true,
    version: 'v2024.1',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'nvd_api'
  },
  {
    name: 'sublist3r',
    capabilityCategory: 'OSINT & RECON',
    binary: '/usr/bin/sublist3r',
    installed: true,
    version: '1.1',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'amass'
  },
  {
    name: 'cyberchef',
    capabilityCategory: 'ENCODING & CRYPTO',
    binary: '/usr/local/bin/cyberchef-cli',
    installed: true,
    version: '10.5.2',
    status: 'READY',
    executionCount: 0,
    lastExecution: 'Never',
    fallbackTool: 'python3'
  }
];

export const INITIAL_AI_DECISIONS: AiDecision[] = [];

export const INITIAL_MODEL_ROUTES: ModelRoute[] = [
  {
    task: 'Reconnaissance & Quick Scans',
    selectedProvider: 'Cerebras',
    model: 'llama-3.3-70b (Ultra High-Speed)',
    reason: 'Ultra low latency required for tool invocation loops',
    latency: '120ms',
    fallback: 'Gemini 1.5 Flash'
  },
  {
    task: 'Web Analysis & Vision',
    selectedProvider: 'Gemini',
    model: 'Gemini 1.5 Pro',
    reason: 'Superior contextual analysis & multi-modal screenshot parsing',
    latency: '420ms',
    fallback: 'OpenRouter / Claude'
  },
  {
    task: 'Code Analysis & Payload Dev',
    selectedProvider: 'NVIDIA',
    model: 'Nemotron 70B',
    reason: 'Strong syntax comprehension & exploit script formulation',
    latency: '310ms',
    fallback: 'Codex'
  },
  {
    task: 'Hard Reasoning & Escalation',
    selectedProvider: 'AgentRouter / Claude Code',
    model: 'Claude 3.5 Sonnet (CLI Agent)',
    reason: 'Escalated complex multi-step reasoning & terminal agent loop',
    latency: '890ms',
    fallback: 'AgentRouter / Codex'
  }
];

export const INITIAL_EVIDENCE: EvidenceItem[] = [];

export const INITIAL_TERMINAL_LOGS: TerminalLog[] = [];

export const INITIAL_PROVIDERS: ProviderInfo[] = [
  {
    name: 'Gemini',
    status: 'HEALTHY',
    model: 'Gemini 1.5 Pro / Flash',
    transport: 'API',
    latency: '420ms',
    requests: 0,
    quota: '100% Available',
    lastError: 'None',
    fallbackPriority: 1,
    routerNote: 'Primary for Web Vision & Multimodal CTF context'
  },
  {
    name: 'NVIDIA',
    status: 'HEALTHY',
    model: 'Nemotron 70B Instruct',
    transport: 'API',
    latency: '310ms',
    requests: 0,
    quota: '100% Available',
    lastError: 'None',
    fallbackPriority: 2,
    routerNote: 'Primary for source code & script generation'
  },
  {
    name: 'Cerebras',
    status: 'HEALTHY',
    model: 'Llama 3.3 70B (Fast)',
    transport: 'API',
    latency: '120ms',
    requests: 0,
    quota: '100% Available',
    lastError: 'None',
    fallbackPriority: 3,
    routerNote: 'Primary for high-frequency tool selection'
  },
  {
    name: 'OpenRouter',
    status: 'HEALTHY',
    model: 'Multi-Provider Aggregator',
    transport: 'API',
    latency: '550ms',
    requests: 0,
    quota: '100% Available',
    lastError: 'None',
    fallbackPriority: 4,
    routerNote: 'Fallback route for broad LLM availability'
  },
  {
    name: 'AgentRouter (Claude Code)',
    status: 'HEALTHY',
    model: 'Claude Opus 5 / 4.8 (CLI Mode)',
    transport: 'CLI',
    latency: '890ms',
    requests: 0,
    quota: 'Batch Quota (07:00 & 19:00 Beijing)',
    lastError: 'None',
    fallbackPriority: 5,
    routerNote: 'CLI → AgentRouter wrapper. QUOTA LIMITED: Claude models use batch quotas released at Beijing 07:00 & 19:00 (UTC 23:00 & 11:00). Auto-falls back to DeepSeek when exhausted.',
    quotaLimited: true,
    quotaExhausted: false,
    quotaFallbackModel: 'deepseek-v4-flash',
    nextBatchTime: ''
  },
  {
    name: 'AgentRouter (Codex – GPT)',
    status: 'HEALTHY',
    model: 'GPT-5.6 / GPT-5.6-SOL (CLI Mode)',
    transport: 'CLI',
    latency: '740ms',
    requests: 0,
    quota: 'Batch Quota (07:00 & 19:00 Beijing)',
    lastError: 'None',
    fallbackPriority: 6,
    routerNote: 'CLI → AgentRouter wrapper. QUOTA LIMITED: GPT models use batch quotas released at Beijing 07:00 & 19:00 (UTC 23:00 & 11:00). Auto-falls back to DeepSeek / GLM when exhausted.',
    quotaLimited: true,
    quotaExhausted: false,
    quotaFallbackModel: 'deepseek-v4-flash',
    nextBatchTime: ''
  },
  {
    name: 'AgentRouter (Codex – DeepSeek)',
    status: 'HEALTHY',
    model: 'DeepSeek V4 Flash (CLI Mode)',
    transport: 'CLI',
    latency: '680ms',
    requests: 0,
    quota: '∞ Always Available',
    lastError: 'None',
    fallbackPriority: 7,
    routerNote: 'CLI → AgentRouter wrapper. NO QUOTA LIMIT: DeepSeek models are always available, unaffected by batch quota system.',
    quotaLimited: false,
    quotaExhausted: false
  },
  {
    name: 'AgentRouter (Codex – GLM)',
    status: 'HEALTHY',
    model: 'GLM-5.3 (CLI Mode)',
    transport: 'CLI',
    latency: '720ms',
    requests: 0,
    quota: '∞ Always Available',
    lastError: 'None',
    fallbackPriority: 8,
    routerNote: 'CLI → AgentRouter wrapper. NO QUOTA LIMIT: GLM models are always available, unaffected by batch quota system.',
    quotaLimited: false,
    quotaExhausted: false
  }
];

export const INITIAL_CHECKPOINTS: CheckpointItem[] = [];

export const INITIAL_AUDIT_LOGS: AuditLog[] = [];

export const INITIAL_FINDINGS: Finding[] = [];

export const INITIAL_WORKFLOW_PIPELINE: WorkflowNode[] = [];
