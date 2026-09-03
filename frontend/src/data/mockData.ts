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

export const INITIAL_CHALLENGES: Challenge[] = [
  {
    id: 'ch-vault-001',
    name: 'VAULT',
    category: 'WEB',
    difficulty: 'HARD',
    target: '10.10.14.23',
    status: 'RUNNING',
    progress: 67,
    lastActivity: '18:42:17 UTC',
    flagStatus: 'UNFOUND',
    description: 'Corporate portal with authentication bypass and SQL injection vulnerabilities.'
  },
  {
    id: 'ch-darkroom-002',
    name: 'DARKROOM',
    category: 'REV',
    difficulty: 'MEDIUM',
    target: '10.10.14.31',
    status: 'PAUSED',
    progress: 42,
    lastActivity: '17:15:02 UTC',
    flagStatus: 'UNFOUND',
    description: 'Obfuscated x86-64 binary keygen algorithm analysis.'
  },
  {
    id: 'ch-matrix-003',
    name: 'MATRIX',
    category: 'PWN',
    difficulty: 'INSANE',
    target: '10.10.14.45',
    status: 'WAITING',
    progress: 0,
    lastActivity: '15:00:00 UTC',
    flagStatus: 'UNFOUND',
    description: 'Heap exploitation on GLIBC 2.35 buffer overflow service.'
  },
  {
    id: 'ch-cipher-004',
    name: 'CIPHER_REALM',
    category: 'CRYPTO',
    difficulty: 'EASY',
    target: '10.10.14.88',
    status: 'SOLVED',
    progress: 100,
    lastActivity: '14:22:10 UTC',
    flagStatus: 'CAPTURED',
    flag: 'HTB{cUsT0m_rSa_p4dD1nG_fl4g}',
    description: 'Custom RSA padding oracle attack.'
  }
];

export const INITIAL_TARGETS: Target[] = [
  {
    id: 'TARGET-0042',
    currentIp: '10.10.14.23',
    hostname: 'vault.ctf',
    services: [
      { port: 22, proto: 'tcp', service: 'SSH', version: 'OpenSSH 8.9p1 Ubuntu' },
      { port: 80, proto: 'tcp', service: 'HTTP', version: 'nginx/1.18.0' },
      { port: 8080, proto: 'tcp', service: 'HTTP-PROXY', version: 'Apache Tomcat 9.0.65' }
    ],
    technologies: ['nginx', 'Apache Tomcat', 'PHP 8.1', 'MySQL', 'Ubuntu Linux'],
    status: 'VERIFIED',
    discoveryMethod: 'nmap SYN Stealth Scan (-sS -sV)',
    lastVerified: '18:42:14 UTC',
    addressHistory: ['10.10.14.19', '10.10.14.23'],
    challengeId: 'ch-vault-001'
  },
  {
    id: 'TARGET-0089',
    currentIp: '10.10.14.31',
    hostname: 'darkroom.ctf',
    services: [
      { port: 2222, proto: 'tcp', service: 'SSH-ALT', version: 'OpenSSH 7.6' },
      { port: 9001, proto: 'tcp', service: 'CUSTOM-REV', version: 'Native ELF Service' }
    ],
    technologies: ['ELF 64-bit LSB Executable', 'GDB Remote', 'Linux 5.15'],
    status: 'VERIFIED',
    discoveryMethod: 'rustscan fast port sweep',
    lastVerified: '17:10:00 UTC',
    addressHistory: ['10.10.14.31'],
    challengeId: 'ch-darkroom-002'
  }
];

export const INITIAL_AGENTS: AgentInfo[] = [
  {
    id: 'ag-orchestrator',
    name: 'ORCHESTRATOR',
    status: 'RUNNING',
    currentObjective: 'Coordinates challenge pipeline & capability routing',
    currentCapability: 'pipeline_management',
    selectedModel: 'Gemini (Router)',
    lastTool: 'workflow_engine',
    lastResult: 'Target profile initialized & RECON dispatched',
    runtime: '00:14:32',
    actionsCompleted: 28,
    failures: 0,
    checkpointStatus: 'HEALTHY'
  },
  {
    id: 'ag-recon',
    name: 'RECON',
    status: 'RUNNING',
    currentObjective: 'Active network sweep & fingerprinting',
    currentCapability: 'network_scan',
    selectedModel: 'Cerebras (Fast Inference)',
    lastTool: 'nmap',
    lastResult: 'Found 3 open ports (22, 80, 8080)',
    runtime: '00:14:15',
    actionsCompleted: 12,
    failures: 0,
    checkpointStatus: 'CHECKPOINT_SAVED'
  },
  {
    id: 'ag-web',
    name: 'WEB',
    status: 'ANALYZING',
    currentObjective: 'Enumerating HTTP attack surface & auth endpoints',
    currentCapability: 'directory_enumeration',
    selectedModel: 'Gemini (Web Vision & Reasoning)',
    lastTool: 'ffuf',
    lastResult: 'Discovered /api/v1/login, /admin, /db_dump.sql',
    runtime: '00:10:48',
    actionsCompleted: 19,
    failures: 1,
    checkpointStatus: 'IN_PROGRESS'
  },
  {
    id: 'ag-forensics',
    name: 'FORENSICS',
    status: 'IDLE',
    currentObjective: 'Memory dump & PCAP artifact analysis',
    currentCapability: 'packet_analysis',
    selectedModel: 'NVIDIA Code Model',
    lastTool: 'tshark',
    lastResult: 'No PCAP file queued',
    runtime: '00:00:00',
    actionsCompleted: 4,
    failures: 0,
    checkpointStatus: 'IDLE'
  },
  {
    id: 'ag-crypto',
    name: 'CRYPTO',
    status: 'STANDBY',
    currentObjective: 'Ciphertext decryption & mathematical analysis',
    currentCapability: 'math_reasoning',
    selectedModel: 'Cerebras',
    lastTool: 'sage',
    lastResult: 'Standby for cryptographic payloads',
    runtime: '00:00:00',
    actionsCompleted: 2,
    failures: 0,
    checkpointStatus: 'STANDBY'
  },
  {
    id: 'ag-pwn',
    name: 'PWN',
    status: 'STANDBY',
    currentObjective: 'Buffer overflow payload crafting',
    currentCapability: 'binary_exploitation',
    selectedModel: 'AgentRouter / Claude Code',
    lastTool: 'pwntools',
    lastResult: 'Awaiting heap memory offsets',
    runtime: '00:00:00',
    actionsCompleted: 0,
    failures: 0,
    checkpointStatus: 'STANDBY'
  },
  {
    id: 'ag-rev',
    name: 'REV',
    status: 'STANDBY',
    currentObjective: 'Decompilation & control-flow analysis',
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
    executionCount: 14,
    lastExecution: '18:42:05 UTC',
    fallbackTool: 'rustscan'
  },
  {
    name: 'rustscan',
    capabilityCategory: 'NETWORK SCANNING',
    binary: '/usr/local/bin/rustscan',
    installed: true,
    version: '2.1.1',
    status: 'READY',
    executionCount: 5,
    lastExecution: '17:10:00 UTC',
    fallbackTool: 'masscan'
  },
  {
    name: 'ffuf',
    capabilityCategory: 'DIRECTORY ENUMERATION',
    binary: '/usr/bin/ffuf',
    installed: true,
    version: 'v2.0.0-dev',
    status: 'EXECUTING',
    executionCount: 22,
    lastExecution: '18:42:15 UTC',
    fallbackTool: 'gobuster'
  },
  {
    name: 'gobuster',
    capabilityCategory: 'DIRECTORY ENUMERATION',
    binary: '/usr/bin/gobuster',
    installed: true,
    version: 'v3.6.0',
    status: 'READY',
    executionCount: 8,
    lastExecution: '16:05:12 UTC',
    fallbackTool: 'feroxbuster'
  },
  {
    name: 'curl',
    capabilityCategory: 'WEB ANALYSIS',
    binary: '/usr/bin/curl',
    installed: true,
    version: '8.5.0',
    status: 'READY',
    executionCount: 45,
    lastExecution: '18:42:16 UTC',
    fallbackTool: 'httpx'
  },
  {
    name: 'browser',
    capabilityCategory: 'WEB ANALYSIS',
    binary: 'playwright/chromium',
    installed: true,
    version: '1.40.0',
    status: 'READY',
    executionCount: 9,
    lastExecution: '18:30:10 UTC',
    fallbackTool: 'curl'
  },
  {
    name: 'tshark',
    capabilityCategory: 'PACKET ANALYSIS',
    binary: '/usr/bin/tshark',
    installed: true,
    version: '4.0.10',
    status: 'READY',
    executionCount: 3,
    lastExecution: '14:12:00 UTC',
    fallbackTool: 'tcpdump'
  },
  {
    name: 'binwalk',
    capabilityCategory: 'FILE ANALYSIS',
    binary: '/usr/bin/binwalk',
    installed: true,
    version: 'v2.3.3',
    status: 'READY',
    executionCount: 7,
    lastExecution: '15:20:44 UTC',
    fallbackTool: 'file'
  }
];

export const INITIAL_AI_DECISIONS: AiDecision[] = [
  {
    id: 'dec-101',
    timestamp: '18:42:01 UTC',
    agent: 'ORCHESTRATOR',
    goal: 'Initialize CTF Target Profile & Scope',
    capability: 'target_profiling',
    selectedTool: 'workflow_engine',
    reason: 'New challenge VAULT selected for autonomous attack.',
    result: 'Target 10.10.14.23 bound to active operational state.',
    nextAction: 'Dispatch RECON agent for port enumeration.',
    confidence: 99,
    challengeId: 'ch-vault-001'
  },
  {
    id: 'dec-102',
    timestamp: '18:42:04 UTC',
    agent: 'RECON AGENT',
    goal: 'Identify exposed network services',
    capability: 'network_scan',
    selectedTool: 'nmap',
    reason: 'Target host has not been profiled for active TCP ports.',
    result: '22/tcp SSH, 80/tcp HTTP, 8080/tcp HTTP discovered.',
    nextAction: 'Hand off HTTP endpoints to WEB AGENT.',
    confidence: 94,
    challengeId: 'ch-vault-001'
  },
  {
    id: 'dec-103',
    timestamp: '18:42:17 UTC',
    agent: 'WEB AGENT',
    goal: 'Examine HTTP attack surface & discover hidden endpoints',
    capability: 'directory_enumeration',
    selectedTool: 'ffuf',
    reason: 'Found HTTP services on ports 80 and 8080. Fuzzing endpoints.',
    result: 'Discovered /login (302), /admin (403), /api/v1/auth (200).',
    nextAction: 'Inspect authentication parameter parsing for SQL Injection.',
    confidence: 87,
    challengeId: 'ch-vault-001'
  }
];

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
    model: 'NVIDIA Nemotron 70B',
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

export const INITIAL_EVIDENCE: EvidenceItem[] = [
  {
    id: 'ev-001',
    timestamp: '18:42:14 UTC',
    source: 'NMAP SCANNER',
    agent: 'RECON',
    tool: 'nmap',
    target: '10.10.14.23',
    type: 'COMMAND OUTPUT',
    description: 'Nmap TCP SYN scan results showing open ports 22, 80, 8080',
    content: 'PORT     STATE SERVICE     VERSION\n22/tcp   open  ssh         OpenSSH 8.9p1 Ubuntu\n80/tcp   open  http        nginx/1.18.0\n8080/tcp open  http-proxy  Apache Tomcat 9.0.65',
    challengeId: 'ch-vault-001'
  },
  {
    id: 'ev-002',
    timestamp: '18:42:17 UTC',
    source: 'FFUF DIRECTORY FUZZER',
    agent: 'WEB',
    tool: 'ffuf',
    target: '10.10.14.23',
    type: 'HTTP RESPONSE',
    description: 'Discovered endpoint /api/v1/auth returning JSON error schema',
    content: 'HTTP/1.1 200 OK\nServer: nginx/1.18.0\nContent-Type: application/json\n\n{"status":"error","message":"Missing parameters: username, password","db":"MySQL 8.0"}',
    challengeId: 'ch-vault-001'
  },
  {
    id: 'ev-003',
    timestamp: '18:42:20 UTC',
    source: 'WEB AGENT INJECTION TEST',
    agent: 'WEB',
    tool: 'curl',
    target: '10.10.14.23',
    type: 'FINDING',
    description: 'SQL Injection confirmed on parameter "username" via error-based payload',
    content: 'POST /api/v1/auth HTTP/1.1\nHost: 10.10.14.23\nContent-Type: application/json\n\n{"username": "admin\' OR 1=1--", "password": "x"}\n\n--> Response 200 OK: {"token": "eyJhbGciOiJIUzI1NiIsInR5cCI6..."}',
    challengeId: 'ch-vault-001'
  }
];

export const INITIAL_TERMINAL_LOGS: TerminalLog[] = [
  {
    id: 'log-01',
    timestamp: '18:42:01',
    type: 'SYSTEM',
    command: 'forge --init --target 10.10.14.23',
    output: '[+] FORGE Autonomous Engine Initialized.\n[+] Target set to 10.10.14.23 (vault.ctf)\n[+] All safety policies loaded [MODE: CTF_OFFENSIVE_CONTROLLED]',
    exitCode: 0,
    duration: '0.12s',
    privilege: 'SAFE'
  },
  {
    id: 'log-02',
    timestamp: '18:42:05',
    type: 'FORGE TOOL EXECUTION',
    command: 'nmap -sV -sC -p 22,80,8080 10.10.14.23',
    output: 'Starting Nmap 7.94 ( https://nmap.org )\nNmap scan report for 10.10.14.23\nHost is up (0.021s latency).\n\nPORT     STATE SERVICE     VERSION\n22/tcp   open  ssh         OpenSSH 8.9p1 Ubuntu\n80/tcp   open  http        nginx/1.18.0\n8080/tcp open  http-proxy  Apache Tomcat 9.0.65\n\nService detection performed.',
    exitCode: 0,
    duration: '8.42s',
    privilege: 'SAFE',
    agent: 'RECON',
    challengeId: 'ch-vault-001'
  },
  {
    id: 'log-03',
    timestamp: '18:42:15',
    type: 'CLAUDE CODE',
    command: 'claude-code analyze-endpoint --url http://10.10.14.23/api/v1/auth',
    output: '[CLAUDE CODE CLI] Analyzing endpoint structure...\n[+] Detected parameters: username, password\n[+] Recommendation: Test for boolean-based and blind SQL injection payloads on username field.',
    exitCode: 0,
    duration: '2.14s',
    privilege: 'SAFE',
    agent: 'WEB',
    challengeId: 'ch-vault-001'
  }
];

export const INITIAL_PROVIDERS: ProviderInfo[] = [
  {
    name: 'Gemini',
    status: 'HEALTHY',
    model: 'Gemini 1.5 Pro / Flash',
    transport: 'API',
    latency: '420ms',
    requests: 142,
    quota: '72% Available',
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
    requests: 88,
    quota: '85% Available',
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
    requests: 215,
    quota: '91% Available',
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
    requests: 34,
    quota: '95% Available',
    lastError: 'None',
    fallbackPriority: 4,
    routerNote: 'Fallback route for broad LLM availability'
  },
  {
    name: 'AgentRouter (Claude Code)',
    status: 'HEALTHY',
    model: 'Claude 3.5 Sonnet (CLI Mode)',
    transport: 'CLI',
    latency: '890ms',
    requests: 19,
    quota: '88% Available',
    lastError: 'None',
    fallbackPriority: 5,
    routerNote: 'CLI → AgentRouter wrapper for interactive command execution'
  },
  {
    name: 'AgentRouter (Codex)',
    status: 'HEALTHY',
    model: 'OpenAI Codex (CLI Mode)',
    transport: 'CLI',
    latency: '740ms',
    requests: 12,
    quota: '90% Available',
    lastError: 'None',
    fallbackPriority: 6,
    routerNote: 'CLI → AgentRouter wrapper for binary/script patch execution'
  },
  {
    name: 'Hugging Face',
    status: 'HEALTHY',
    model: 'Local / Inference API',
    transport: 'API',
    latency: '620ms',
    requests: 5,
    quota: '100% Available',
    lastError: 'None',
    fallbackPriority: 7
  },
  {
    name: 'Cloudflare Workers AI',
    status: 'HEALTHY',
    model: 'Edge Llama 3 8B',
    transport: 'API',
    latency: '190ms',
    requests: 8,
    quota: '99% Available',
    lastError: 'None',
    fallbackPriority: 8
  }
];

export const INITIAL_CHECKPOINTS: CheckpointItem[] = [
  {
    runId: 'RUN #0042',
    challenge: 'VAULT',
    checkpointName: 'HTTP enumeration completed',
    status: 'ACTIVE',
    created: '18:42:15 UTC',
    reason: 'Phase transition: Recon → Vulnerability Exploitation',
    completedSteps: [
      'Target network profiling',
      'SYN stealth port scanning',
      'HTTP service banner grabbing',
      'Fuzzing authentication endpoints'
    ],
    pendingSteps: [
      'SQL injection payload automated verification',
      'Session token decoding & forge testing',
      'Privilege escalation to root shell',
      'Flag retrieval from /root/flag.txt'
    ]
  },
  {
    runId: 'RUN #0039',
    challenge: 'DARKROOM',
    checkpointName: 'Binary reverse engineering checkpoint #1',
    status: 'PAUSED',
    created: '17:15:00 UTC',
    reason: 'Operator paused execution for manual inspection',
    completedSteps: [
      'ELF header parsing',
      'Ghidra headless decompilation',
      'Keygen algorithm function extraction'
    ],
    pendingSteps: [
      'Z3 solver constraint modeling',
      'Keygen script execution',
      'Submit generated flag'
    ]
  }
];

export const INITIAL_AUDIT_LOGS: AuditLog[] = [
  {
    id: 'aud-1001',
    timestamp: '18:42:05 UTC',
    actor: 'Tool Manager',
    action: 'nmap -sV -sC 10.10.14.23',
    target: '10.10.14.23',
    permission: 'SAFE',
    result: 'SUCCESS'
  },
  {
    id: 'aud-1002',
    timestamp: '18:42:15 UTC',
    actor: 'Tool Manager',
    action: 'ffuf -u http://10.10.14.23/FUZZ -w /usr/share/wordlists/dirb/common.txt',
    target: '10.10.14.23',
    permission: 'SAFE',
    result: 'SUCCESS'
  },
  {
    id: 'aud-1003',
    timestamp: '18:42:17 UTC',
    actor: 'AgentRouter',
    action: 'claude-code analyze-endpoint --url http://10.10.14.23/api/v1/auth',
    target: '10.10.14.23',
    permission: 'SAFE',
    result: 'SUCCESS'
  }
];

export const INITIAL_FINDINGS: Finding[] = [
  {
    id: 'find-001',
    severity: 'CRITICAL',
    title: 'SQL INJECTION IN AUTHENTICATION ENDPOINT',
    target: '10.10.14.23',
    endpoint: '/api/v1/auth',
    status: 'VERIFIED',
    evidenceRef: 'ev-003',
    description: 'Parameter "username" is directly concatenated into MySQL query string without parameterization, enabling authentication bypass.',
    challengeId: 'ch-vault-001'
  },
  {
    id: 'find-002',
    severity: 'MEDIUM',
    title: 'INFORMATION DISCLOSURE IN ERROR RESPONSE',
    target: '10.10.14.23',
    endpoint: '/api/v1/auth',
    status: 'VERIFIED',
    evidenceRef: 'ev-002',
    description: 'HTTP 200 JSON payload discloses exact database engine ("MySQL 8.0") and internal table schema names.',
    challengeId: 'ch-vault-001'
  }
];

export const INITIAL_WORKFLOW_PIPELINE: WorkflowNode[] = [
  {
    id: 'wf-1',
    label: 'TARGET DISCOVERED',
    status: 'COMPLETED',
    description: 'Target IP 10.10.14.23 confirmed reachable via ping ICMP.'
  },
  {
    id: 'wf-2',
    label: 'TARGET PROFILE',
    status: 'COMPLETED',
    description: 'Hostname vault.ctf registered, Linux target profile built.'
  },
  {
    id: 'wf-3',
    label: 'PORT SCAN',
    status: 'COMPLETED',
    description: 'Open ports identified: 22 (SSH), 80 (HTTP), 8080 (HTTP-Proxy).',
    evidenceId: 'ev-001'
  },
  {
    id: 'wf-4',
    label: 'HTTP ENUMERATION',
    status: 'COMPLETED',
    description: 'Fuzzing endpoints completed; found /api/v1/auth.',
    evidenceId: 'ev-002'
  },
  {
    id: 'wf-5',
    label: 'TECHNOLOGY IDENTIFICATION',
    status: 'COMPLETED',
    description: 'nginx 1.18.0, Apache Tomcat 9.0.65, MySQL database confirmed.'
  },
  {
    id: 'wf-6',
    label: 'HYPOTHESIS',
    status: 'ACTIVE',
    description: 'Authentication endpoint /api/v1/auth is vulnerable to SQL injection.'
  },
  {
    id: 'wf-7',
    label: 'EXPLOITATION',
    status: 'ACTIVE',
    description: 'Crafting SQLi authentication bypass payload & JWT extract script.',
    evidenceId: 'ev-003'
  },
  {
    id: 'wf-8',
    label: 'VERIFICATION',
    status: 'PENDING',
    description: 'Verify administrative session token & access restricted dashboard.'
  },
  {
    id: 'wf-9',
    label: 'FLAG FOUND',
    status: 'PENDING',
    description: 'Extract flag file from target filesystem.'
  }
];
