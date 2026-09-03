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
}

export const apiService = new ApiService();
