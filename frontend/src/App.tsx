import React, { useState, useEffect } from 'react';
import { 
  ShieldAlert, 
  Terminal, 
  Cpu, 
  Database, 
  Activity, 
  Play, 
  Pause, 
  OctagonAlert, 
  CheckCircle2, 
  Clock, 
  Layers, 
  Server,
  FileText
} from 'lucide-react';

export default function App() {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'decisions' | 'terminal' | 'providers' | 'evidence'>('dashboard');
  const [systemHealth, setSystemHealth] = useState<any>(null);
  const [challenges, setChallenges] = useState<any[]>([]);
  const [tools, setTools] = useState<any[]>([]);
  const [providers, setProviders] = useState<any[]>([]);
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [activeRunStatus, setActiveRunStatus] = useState<string>("IDLE");

  // Form states for creating challenge
  const [newChallengeName, setNewChallengeName] = useState("");
  const [newTargetAddr, setNewTargetAddr] = useState("");
  const [newCategory, setNewCategory] = useState("web");

  useEffect(() => {
    fetchHealth();
    fetchChallenges();
    fetchTools();
    fetchProviders();

    // WebSocket real-time updates setup
    const ws = new WebSocket(`ws://${window.location.host}/ws/events`);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.event === "RUN_STARTED") {
          setActiveRunStatus("RUNNING");
        } else if (data.event === "KILL_SWITCH_ACTIVATED") {
          setKillSwitchActive(true);
          setActiveRunStatus("CANCELLED");
        }
      } catch (err) {
        console.error("WS Parse error", err);
      }
    };

    return () => ws.close();
  }, []);

  const fetchHealth = async () => {
    try {
      const res = await fetch('/api/health');
      if (res.ok) setSystemHealth(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchChallenges = async () => {
    try {
      const res = await fetch('/api/challenges');
      if (res.ok) setChallenges(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchTools = async () => {
    try {
      const res = await fetch('/api/tools');
      if (res.ok) setTools(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchProviders = async () => {
    try {
      const res = await fetch('/api/providers');
      if (res.ok) setProviders(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const handleCreateChallenge = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newChallengeName || !newTargetAddr) return;

    try {
      const res = await fetch('/api/challenges', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newChallengeName,
          category: newCategory,
          target_address: newTargetAddr
        })
      });
      if (res.ok) {
        setNewChallengeName("");
        setNewTargetAddr("");
        fetchChallenges();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleStartRun = async (challengeId: string) => {
    try {
      const res = await fetch(`/api/runs/${challengeId}/start`, { method: 'POST' });
      if (res.ok) {
        setActiveRunStatus("RUNNING");
        fetchChallenges();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleKillSwitch = async () => {
    try {
      const res = await fetch('/api/killswitch', { method: 'POST' });
      if (res.ok) {
        setKillSwitchActive(true);
        setActiveRunStatus("HALTED");
      }
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100 font-sans">
      {/* Top Cyber Command Header */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur-md px-6 py-3 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <div className="w-8 h-8 rounded-lg bg-cyan-500/20 border border-cyan-400 flex items-center justify-center text-cyan-400 font-bold font-mono">
              F
            </div>
            <div>
              <h1 className="font-bold text-lg tracking-wider text-slate-100 font-mono">FORGE</h1>
              <p className="text-xs text-slate-400">Autonomous CTF Command Center</p>
            </div>
          </div>

          <div className="h-6 w-px bg-slate-800"></div>

          {/* System Status Indicators */}
          <div className="flex items-center space-x-3 text-xs">
            <span className="flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-slate-800 border border-slate-700 text-slate-300">
              <Server className="w-3.5 h-3.5 text-cyan-400" />
              <span>OS: {systemHealth?.distro || systemHealth?.system || "Loading..."}</span>
            </span>

            <span className="flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-slate-800 border border-slate-700 text-slate-300">
              <Cpu className="w-3.5 h-3.5 text-emerald-400" />
              <span>Tools: {systemHealth?.installed_tools_count || 0} Ready</span>
            </span>

            <span className="flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-slate-800 border border-slate-700 text-slate-300">
              <Activity className="w-3.5 h-3.5 text-amber-400" />
              <span>Budget Cap: ${systemHealth?.daily_budget_usd || 5.00}</span>
            </span>
          </div>
        </div>

        {/* Emergency Kill Switch Button */}
        <div className="flex items-center space-x-3">
          <button
            onClick={handleKillSwitch}
            className={`px-4 py-2 rounded-lg font-bold text-xs flex items-center space-x-2 transition-all duration-200 ${
              killSwitchActive
                ? "bg-red-950 border border-red-600 text-red-400 animate-pulse"
                : "bg-red-600 hover:bg-red-500 text-white shadow-lg shadow-red-600/30"
            }`}
          >
            <OctagonAlert className="w-4 h-4" />
            <span>{killSwitchActive ? "KILL SWITCH ACTIVATED" : "EMERGENCY KILL SWITCH"}</span>
          </button>
        </div>
      </header>

      {/* Main Content Layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar Navigation */}
        <aside className="w-64 border-r border-slate-800 bg-slate-900/40 p-4 space-y-2 flex flex-col">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider px-3 mb-2 font-mono">
            Navigation
          </div>
          
          <button
            onClick={() => setActiveTab('dashboard')}
            className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'dashboard' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
            }`}
          >
            <Layers className="w-4 h-4" />
            <span>Dashboard & Targets</span>
          </button>

          <button
            onClick={() => setActiveTab('decisions')}
            className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'decisions' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
            }`}
          >
            <Cpu className="w-4 h-4" />
            <span>AI Decisions & Reasoning</span>
          </button>

          <button
            onClick={() => setActiveTab('terminal')}
            className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'terminal' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
            }`}
          >
            <Terminal className="w-4 h-4" />
            <span>Tool Manager & Console</span>
          </button>

          <button
            onClick={() => setActiveTab('providers')}
            className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'providers' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
            }`}
          >
            <Server className="w-4 h-4" />
            <span>LLM Provider Router</span>
          </button>

          <button
            onClick={() => setActiveTab('evidence')}
            className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'evidence' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
            }`}
          >
            <FileText className="w-4 h-4" />
            <span>Evidence & Reports</span>
          </button>
        </aside>

        {/* Dynamic Tab Body */}
        <main className="flex-1 p-6 overflow-y-auto">
          {activeTab === 'dashboard' && (
            <div className="space-y-6">
              {/* Challenge Creator */}
              <div className="glass-panel rounded-xl p-6 border border-slate-800">
                <h2 className="text-md font-semibold text-slate-100 mb-4 flex items-center space-x-2 font-mono">
                  <Play className="w-4 h-4 text-cyan-400" />
                  <span>Create CTF Challenge Workspace</span>
                </h2>
                <form onSubmit={handleCreateChallenge} className="grid grid-cols-1 md:grid-cols-4 gap-4">
                  <input
                    type="text"
                    placeholder="Challenge Name (e.g. Web Vulnerability #1)"
                    value={newChallengeName}
                    onChange={(e) => setNewChallengeName(e.target.value)}
                    className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
                  />
                  <input
                    type="text"
                    placeholder="Target IP / Host (e.g. 10.10.10.42)"
                    value={newTargetAddr}
                    onChange={(e) => setNewTargetAddr(e.target.value)}
                    className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
                  />
                  <select
                    value={newCategory}
                    onChange={(e) => setNewCategory(e.target.value)}
                    className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="web">Web Exploitation</option>
                    <option value="recon">Reconnaissance</option>
                    <option value="crypto">Cryptography</option>
                    <option value="forensics">Forensics</option>
                    <option value="pwn">Binary / Pwn</option>
                    <option value="rev">Reverse Engineering</option>
                  </select>
                  <button
                    type="submit"
                    className="bg-cyan-600 hover:bg-cyan-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition-colors flex items-center justify-center space-x-2"
                  >
                    <span>Initialize Workspace</span>
                  </button>
                </form>
              </div>

              {/* Active Challenges List */}
              <div className="glass-panel rounded-xl p-6 border border-slate-800">
                <h2 className="text-md font-semibold text-slate-100 mb-4 font-mono">Active Challenge Workspaces</h2>
                {challenges.length === 0 ? (
                  <p className="text-slate-500 text-sm italic">No challenges initialized yet. Create one above to start investigation.</p>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {challenges.map((ch) => (
                      <div key={ch.id} className="bg-slate-900/60 border border-slate-800 rounded-lg p-4 flex flex-col justify-between">
                        <div>
                          <div className="flex items-center justify-between mb-2">
                            <h3 className="font-semibold text-slate-200">{ch.name}</h3>
                            <span className="text-xs px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 uppercase font-mono">{ch.category}</span>
                          </div>
                          <p className="text-xs text-slate-400">Status: <span className="text-slate-200 font-mono">{ch.status}</span></p>
                        </div>
                        <div className="mt-4 pt-3 border-t border-slate-800 flex items-center justify-between">
                          <button
                            onClick={() => handleStartRun(ch.id)}
                            className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center space-x-1.5"
                          >
                            <Play className="w-3.5 h-3.5" />
                            <span>Launch Autonomous Run</span>
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'providers' && (
            <div className="space-y-4">
              <h2 className="text-md font-semibold text-slate-100 font-mono">Configured LLM Providers</h2>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {providers.map((p) => (
                  <div key={p.name} className="glass-panel border border-slate-800 rounded-xl p-4 flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold capitalize text-slate-200">{p.name}</h3>
                      <p className="text-xs text-slate-400">{p.is_paid ? "Paid Model" : "Free / Local Tier"}</p>
                    </div>
                    <span className="px-2 py-1 text-xs rounded bg-emerald-950 border border-emerald-800 text-emerald-400 font-mono">
                      {p.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'terminal' && (
            <div className="space-y-4">
              <h2 className="text-md font-semibold text-slate-100 font-mono">Capability Tool Manager & Controlled Console</h2>
              <div className="glass-panel border border-slate-800 rounded-xl p-4">
                <div className="text-xs font-mono text-slate-400 mb-3">Registered Approved Tools ({tools.length})</div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {tools.map((t) => (
                    <div key={t.tool_name} className="bg-slate-900 border border-slate-800 p-3 rounded-lg text-xs font-mono">
                      <div className="flex items-center justify-between text-cyan-400 font-bold">
                        <span>{t.tool_name}</span>
                        <span className="text-slate-500 text-[10px]">{t.privilege_requirement}</span>
                      </div>
                      <div className="text-slate-400 mt-1">Binary: {t.binary}</div>
                      <div className="text-slate-500 mt-1">Capabilities: {t.capabilities.join(", ")}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'decisions' && (
            <div className="glass-panel border border-slate-800 rounded-xl p-6">
              <h2 className="text-md font-semibold text-slate-100 mb-2 font-mono">AI Decisions & Capability Requests</h2>
              <p className="text-xs text-slate-400 mb-4">Live inspection feed displaying agent capabilities requested, tool resolutions, and reasoning paths.</p>
              <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 font-mono text-xs text-slate-300">
                [ORCHESTRATOR] Autonomous agent loop initialized. Standby for target capability requests...
              </div>
            </div>
          )}

          {activeTab === 'evidence' && (
            <div className="glass-panel border border-slate-800 rounded-xl p-6">
              <h2 className="text-md font-semibold text-slate-100 mb-2 font-mono">Collected Evidence & Writeup Reports</h2>
              <p className="text-xs text-slate-400">Captured flags, HTTP header outputs, screenshots, and automatic Markdown reports.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
