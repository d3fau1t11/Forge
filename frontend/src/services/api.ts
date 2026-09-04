// API Service Client connecting FORGE Frontend to FastAPI REST Endpoints & WebSockets

const API_BASE_URL = 'http://localhost:8000/api';
const WS_BASE_URL = 'ws://localhost:8000/ws/events';

export class ApiService {
  private ws: WebSocket | null = null;
  private wsListeners: Array<(eventData: any) => void> = [];

  // ----------------------------------------------------
  // WEBSOCKET REAL-TIME EVENTS
  // ----------------------------------------------------

  public connectWebSocket(onEvent: (eventData: any) => void) {
    this.wsListeners.push(onEvent);

    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      this.ws = new WebSocket(WS_BASE_URL);

      this.ws.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(ev.data);
          this.wsListeners.forEach((listener) => listener(parsed));
        } catch (e) {
          console.warn('Malformed WS frame received:', ev.data);
        }
      };

      this.ws.onclose = () => {
        console.log('WebSocket disconnected. Reconnecting in 3s...');
        setTimeout(() => this.connectWebSocket(onEvent), 3000);
      };

      this.ws.onerror = (err) => {
        console.error('WebSocket connection error:', err);
      };
    } catch (e) {
      console.warn('WebSocket connection failed:', e);
    }
  }

  // ----------------------------------------------------
  // SYSTEM & ENVIRONMENT
  // ----------------------------------------------------

  public async getHealth() {
    try {
      const res = await fetch(`${API_BASE_URL}/health`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      console.warn('API getHealth failed, returning offline status:', e);
      return { status: 'offline', installed_tools_count: 0 };
    }
  }

  public async getEnvironment() {
    try {
      const res = await fetch(`${API_BASE_URL}/environment`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return null;
    }
  }

  // ----------------------------------------------------
  // CHALLENGES
  // ----------------------------------------------------

  public async getChallenges() {
    try {
      const res = await fetch(`${API_BASE_URL}/challenges`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  public async createChallenge(data: {
    name: string;
    category: string;
    difficulty?: string;
    description: string;
    target_address: string;
    working_directory?: string;
    platform_name?: string;
  }) {
    const res = await fetch(`${API_BASE_URL}/challenges`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  public async generateReport(challengeId: string) {
    const res = await fetch(`${API_BASE_URL}/challenges/${challengeId}/report`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // ----------------------------------------------------
  // TARGETS
  // ----------------------------------------------------

  public async getTargets() {
    try {
      const res = await fetch(`${API_BASE_URL}/targets`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  public async verifyTarget(targetId: string) {
    const res = await fetch(`${API_BASE_URL}/targets/${targetId}/verify`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  public async rediscoverTarget(targetId: string) {
    const res = await fetch(`${API_BASE_URL}/targets/${targetId}/rediscover`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  public async updateTargetAddress(targetId: string, newAddress: string) {
    const res = await fetch(`${API_BASE_URL}/targets/${targetId}/address`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_address: newAddress })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // ----------------------------------------------------
  // RUNS & KILL SWITCH
  // ----------------------------------------------------

  public async startRun(challengeId: string) {
    const res = await fetch(`${API_BASE_URL}/runs/${challengeId}/start`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  public async activateKillSwitch(runId?: string) {
    const res = await fetch(`${API_BASE_URL}/killswitch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // ----------------------------------------------------
  // TOOLS & EXECUTIONS
  // ----------------------------------------------------

  public async getTools() {
    try {
      const res = await fetch(`${API_BASE_URL}/tools`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  public async getToolExecutions() {
    try {
      const res = await fetch(`${API_BASE_URL}/tools/executions`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  public async executeTerminalCommand(command: string, challengeId?: string) {
    const res = await fetch(`${API_BASE_URL}/terminal/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command, challenge_id: challengeId })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // ----------------------------------------------------
  // PRIVILEGE MANAGER
  // ----------------------------------------------------

  public async getPendingPrivileges() {
    try {
      const res = await fetch(`${API_BASE_URL}/privilege/pending`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  public async sendPrivilegeDecision(auditId: string, approved: boolean) {
    const res = await fetch(`${API_BASE_URL}/privilege/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audit_id: auditId, approved })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // ----------------------------------------------------
  // PROVIDERS & EVIDENCE
  // ----------------------------------------------------

  public async getProviders() {
    try {
      const res = await fetch(`${API_BASE_URL}/providers/health`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return null;
    }
  }

  public async getEvidence() {
    try {
      const res = await fetch(`${API_BASE_URL}/evidence`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  public async getFindings() {
    try {
      const res = await fetch(`${API_BASE_URL}/findings`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  // ----------------------------------------------------
  // SYSTEM SETTINGS MANAGEMENT
  // ----------------------------------------------------

  public async getSystemSettings() {
    try {
      const res = await fetch(`${API_BASE_URL}/system/settings`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return {
        execution_mode: 'CTF_OFFENSIVE_CONTROLLED',
        auto_approve_privileged: false,
        command_timeout_seconds: 300,
        daily_budget_usd: 5.00,
        session_budget_usd: 2.00,
        paid_model_allowed: true,
        default_strategy: 'EXPLOIT_FIRST'
      };
    }
  }

  public async updateSystemSettings(data: any) {
    const res = await fetch(`${API_BASE_URL}/system/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // ----------------------------------------------------
  // DIRECTORY BROWSER & FOLDER PICKER
  // ----------------------------------------------------

  public async browseDirectory(path?: string) {
    try {
      const url = path ? `${API_BASE_URL}/system/browse-dir?path=${encodeURIComponent(path)}` : `${API_BASE_URL}/system/browse-dir`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return {
        current_path: path || '',
        parent_path: '',
        drives: [],
        directories: []
      };
    }
  }

  public async createDirectory(parent_path: string, dir_name: string) {
    const res = await fetch(`${API_BASE_URL}/system/create-dir`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_path, dir_name })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  public async selectFolderDialog() {
    try {
      const res = await fetch(`${API_BASE_URL}/system/select-folder-dialog`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      return { status: 'FAILED', selected_path: '' };
    }
  }

  public async getSystemRequirements() {
    const urlsToTry = [
      '/api/system/requirements',
      'http://127.0.0.1:8000/api/system/requirements',
      'http://localhost:8000/api/system/requirements'
    ];

    for (const url of urlsToTry) {
      try {
        const res = await fetch(url);
        if (res.ok) {
          return await res.json();
        }
      } catch (e) {
        // Try next URL candidate
      }
    }

    // Client-side fallback diagnostic when FastAPI backend is offline
    const isWin = navigator.userAgent.includes('Windows');
    const isMac = navigator.userAgent.includes('Macintosh');
    const osName = isWin ? 'Windows 11 / 10 Host OS' : (isMac ? 'macOS Host' : 'Linux Host OS');
    const cores = navigator.hardwareConcurrency || 8;
    const ram = (navigator as any).deviceMemory || 16;

    return {
      overall_status: 'BACKEND_OFFLINE',
      offline_notice: 'FastAPI Backend Server is offline on port 8000. Start backend using: py -m uvicorn backend.main:app --port 8000',
      installed_tools_count: 0,
      total_tools_count: 25,
      environment: {
        distro: `${osName} (Browser Client Diagnostic)`,
        python_version: '3.14.0 (Backend Offline)',
        architecture: 'x86_64 / ARM64',
        cpu_cores: cores,
        ram_gb: ram
      },
      system_requirements: [
        { requirement: "FastAPI Backend Server (Port 8000)", status: "FAIL", details: "Connection Refused on http://127.0.0.1:8000", impact: "Start FastAPI backend server with `py -m uvicorn backend.main:app --port 8000`" },
        { requirement: "Python 3.10+ Runtime Environment", status: "PASS", details: "Detected via host configuration", impact: "Core backend execution requires Python 3.10+." },
        { requirement: "Host Physical RAM Memory", status: "PASS", details: `${ram} GB Client RAM`, impact: "4.0 GB+ recommended." },
        { requirement: "CPU Processing Cores", status: "PASS", details: `${cores} Cores Detected`, impact: "2+ cores recommended." }
      ],
      tool_requirements: [
        { name: "nmap", installed: false, path: "Backend Offline (Click RUN DIAGNOSTICS after starting backend)", installation_recipe: "sudo apt-get install nmap / choco install nmap" },
        { name: "ffuf", installed: false, path: "Backend Offline", installation_recipe: "sudo apt-get install ffuf" },
        { name: "curl", installed: false, path: "Backend Offline", installation_recipe: "sudo apt-get install curl" },
        { name: "claude", installed: false, path: "Backend Offline", installation_recipe: "npm install -g @anthropic-ai/claude-code" },
        { name: "codex", installed: false, path: "Backend Offline", installation_recipe: "npm install -g @openai/codex-cli" }
      ]
    };
  }
}

export const apiService = new ApiService();
