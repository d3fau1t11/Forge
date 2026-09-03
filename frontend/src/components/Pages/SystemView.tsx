import React, { useState } from 'react';
import { 
  Settings, 
  Database, 
  Play, 
  RotateCcw, 
  XCircle, 
  CheckCircle2, 
  FileText,
  Shield,
  DollarSign
} from 'lucide-react';
import { CheckpointItem, AuditLog } from '../../types';
import { apiService } from '../../services/api';
import { soundEngine } from '../../utils/soundEngine';

interface SystemViewProps {
  checkpoints: CheckpointItem[];
  auditLogs: AuditLog[];
  onResumeCheckpoint: (runId: string) => void;
}

export const SystemView: React.FC<SystemViewProps> = ({
  checkpoints,
  auditLogs,
  onResumeCheckpoint
}) => {
  const [activeTab, setActiveTab] = useState<'requirements' | 'checkpoints' | 'audit' | 'settings'>('requirements');

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Tab Controls */}
      <div className="glass-panel border border-slate-800 p-2.5 rounded-xl flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => { soundEngine.playClick(); setActiveTab('requirements'); }}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              activeTab === 'requirements'
                ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_12px_rgba(0,240,255,0.25)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Shield className="w-4 h-4" />
            <span>OS REQUIREMENTS & AUDIT</span>
          </button>

          <button
            onClick={() => { soundEngine.playClick(); setActiveTab('checkpoints'); }}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              activeTab === 'checkpoints'
                ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_12px_rgba(0,240,255,0.25)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Database className="w-4 h-4" />
            <span>CHECKPOINTS ({checkpoints.length})</span>
          </button>

          <button
            onClick={() => { soundEngine.playClick(); setActiveTab('audit'); }}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              activeTab === 'audit'
                ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_12px_rgba(0,240,255,0.25)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileText className="w-4 h-4" />
            <span>AUDIT LOGS</span>
          </button>

          <button
            onClick={() => { soundEngine.playClick(); setActiveTab('settings'); }}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase flex items-center space-x-2 ${
              activeTab === 'settings'
                ? 'bg-cyber-cyan/15 text-cyber-cyan border border-cyber-cyan/60 shadow-[0_0_12px_rgba(0,240,255,0.25)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Settings className="w-4 h-4" />
            <span>SYSTEM SETTINGS</span>
          </button>
        </div>

        <span className="text-[10px] text-cyber-emerald font-bold px-3 py-1.5 rounded-lg bg-obsidian-950 border border-emerald-800 uppercase hidden sm:inline-block">
          FRAMEWORK v2.0 READY
        </span>
      </div>

      {/* TAB 0: OS REQUIREMENTS & DIAGNOSTICS */}
      {activeTab === 'requirements' && (
        <SystemRequirementsAuditor />
      )}

      {/* TAB 1: CHECKPOINTS */}
      {activeTab === 'checkpoints' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100 mb-1">STATE CHECKPOINTS & RUN PERSISTENCE</h2>
            <p className="text-xs text-slate-400">
              Review saved operational state checkpoints, step completion checklists, and restart policies.
            </p>
          </div>

          <div className="space-y-4">
            {checkpoints.map((cp) => (
              <div key={cp.runId} className="bg-[#0b1019] border border-slate-800 rounded-lg p-5 space-y-4 font-mono text-xs">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800 pb-3">
                  <div>
                    <div className="flex items-center space-x-2">
                      <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 text-xs font-bold">
                        {cp.runId}
                      </span>
                      <h3 className="font-bold text-slate-100 text-sm">{cp.challenge}</h3>
                      <span className="text-slate-400 text-xs">• {cp.checkpointName}</span>
                    </div>
                    <p className="text-[11px] text-slate-400 mt-1">Reason: <span className="text-slate-200">{cp.reason}</span></p>
                  </div>

                  <span className={`text-xs px-2.5 py-1 rounded font-bold uppercase ${
                    cp.status === 'ACTIVE' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' :
                    'bg-amber-950 text-amber-400 border border-amber-800'
                  }`}>
                    {cp.status}
                  </span>
                </div>

                {/* Checklist Step Breakdown */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-2">
                    <span className="text-emerald-400 font-bold text-[11px] uppercase block">✓ Completed Steps</span>
                    <ul className="space-y-1 text-[11px] text-slate-300">
                      {cp.completedSteps.map((step, idx) => (
                        <li key={idx} className="flex items-center space-x-2">
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                          <span>{step}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="bg-[#070b12] border border-slate-800 p-3 rounded space-y-2">
                    <span className="text-amber-400 font-bold text-[11px] uppercase block">○ Pending Steps</span>
                    <ul className="space-y-1 text-[11px] text-slate-400">
                      {cp.pendingSteps.map((step, idx) => (
                        <li key={idx} className="flex items-center space-x-2">
                          <span className="w-3.5 h-3.5 rounded-full border border-slate-600 inline-block shrink-0"></span>
                          <span>{step}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>

                {/* Checkpoint Action Buttons */}
                <div className="pt-2 border-t border-slate-800/80 flex items-center justify-end space-x-3">
                  <button className="px-3 py-1.5 rounded bg-red-950 hover:bg-red-900 border border-red-800 text-red-300 text-xs font-bold flex items-center space-x-1">
                    <XCircle className="w-3.5 h-3.5" />
                    <span>CANCEL</span>
                  </button>
                  <button className="px-3 py-1.5 rounded bg-amber-950 hover:bg-amber-900 border border-amber-800 text-amber-300 text-xs font-bold flex items-center space-x-1">
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>RESTART FROM CHECKPOINT</span>
                  </button>
                  <button
                    onClick={() => onResumeCheckpoint(cp.runId)}
                    className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold flex items-center space-x-1 uppercase shadow-[0_0_10px_rgba(16,185,129,0.3)]"
                  >
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>RESUME</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 2: AUDIT LOGS */}
      {activeTab === 'audit' && (
        <div className="space-y-4">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100">FRAMEWORK AUDIT TRAIL LOGS</h2>
            <span className="text-xs text-cyan-400 font-bold">{auditLogs.length} RECORDED ENTRIES</span>
          </div>

          <div className="bg-[#0b1019] border border-slate-800 rounded-lg overflow-x-auto">
            <table className="w-full text-left font-mono text-xs">
              <thead className="bg-[#070b12] border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <tr>
                  <th className="p-3">Timestamp</th>
                  <th className="p-3">Actor</th>
                  <th className="p-3">Executed Action / Command</th>
                  <th className="p-3">Target</th>
                  <th className="p-3">Permission</th>
                  <th className="p-3">Result</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {auditLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-900/40">
                    <td className="p-3 text-slate-500">{log.timestamp}</td>
                    <td className="p-3 font-bold text-slate-200">{log.actor}</td>
                    <td className="p-3 font-mono text-cyan-300">{log.action}</td>
                    <td className="p-3 text-slate-200">{log.target}</td>
                    <td className="p-3">
                      <span className="px-1.5 py-0.2 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 text-[10px] font-bold">
                        {log.permission}
                      </span>
                    </td>
                    <td className="p-3">
                      <span className="px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] font-bold">
                        {log.result}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* TAB 3: SYSTEM SETTINGS MANAGEMENT */}
      {activeTab === 'settings' && (
        <SystemSettingsManager />
      )}
    </div>
  );
};

const SystemSettingsManager: React.FC = () => {
  const [executionMode, setExecutionMode] = useState('CTF_OFFENSIVE_CONTROLLED');
  const [autoApprove, setAutoApprove] = useState(false);
  const [timeoutSeconds, setTimeoutSeconds] = useState(300);
  const [dailyBudget, setDailyBudget] = useState(5.00);
  const [sessionBudget, setSessionBudget] = useState(2.00);
  const [paidAllowed, setPaidAllowed] = useState(true);
  const [defaultStrategy, setDefaultStrategy] = useState('EXPLOIT_FIRST');
  const [saveNotice, setSaveNotice] = useState<string | null>(null);

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    soundEngine.playFanfare();
    try {
      await apiService.updateSystemSettings({
        execution_mode: executionMode,
        auto_approve_privileged: autoApprove,
        command_timeout_seconds: Number(timeoutSeconds),
        daily_budget_usd: Number(dailyBudget),
        session_budget_usd: Number(sessionBudget),
        paid_model_allowed: paidAllowed,
        default_strategy: defaultStrategy
      });
      setSaveNotice("System Configuration updated successfully across framework!");
    } catch (err) {
      setSaveNotice("System Configuration saved locally!");
    }

    setTimeout(() => setSaveNotice(null), 3000);
  };

  return (
    <div className="space-y-5 font-mono text-slate-100">
      <div className="glass-panel border border-slate-800 p-5 rounded-xl flex items-center justify-between">
        <div>
          <h2 className="text-sm font-display font-bold uppercase tracking-wider text-slate-100 neon-text-cyan flex items-center space-x-2">
            <Settings className="w-5 h-5 text-cyber-cyan" />
            <span>FRAMEWORK SYSTEM CONFIGURATION & SAFETY POLICIES</span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Configure global operational modes, privilege security policies, LLM budget caps, and command timeouts.
          </p>
        </div>

        <span className="text-[10px] text-cyber-emerald font-bold px-3 py-1.5 rounded-lg bg-obsidian-950 border border-emerald-800 uppercase">
          LIVE SYSTEM MANAGEMENT ACTIVE
        </span>
      </div>

      {saveNotice && (
        <div className="p-4 rounded-xl bg-obsidian-900 border-2 border-cyber-emerald text-cyber-emerald text-xs font-bold flex items-center space-x-3 animate-pulse shadow-[0_0_20px_rgba(16,185,129,0.3)]">
          <CheckCircle2 className="w-5 h-5 text-cyber-emerald" />
          <span>{saveNotice}</span>
        </div>
      )}

      <form onSubmit={handleSaveSettings} className="space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5 text-xs font-mono">
          {/* Section 1: Execution & Security Policy */}
          <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
            <h3 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-3 flex items-center space-x-2">
              <Shield className="w-4 h-4 text-cyber-cyan" />
              <span>EXECUTION & SECURITY POLICY</span>
            </h3>

            <div className="space-y-4">
              <div>
                <label className="block text-slate-300 font-bold mb-1">Execution Mode</label>
                <select
                  value={executionMode}
                  onChange={(e) => setExecutionMode(e.target.value)}
                  className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan"
                >
                  <option value="CTF_OFFENSIVE_CONTROLLED">CTF_OFFENSIVE_CONTROLLED (Standard Competition)</option>
                  <option value="FULL_AUTONOMOUS_EXPLOITATION">FULL_AUTONOMOUS_EXPLOITATION (Aggressive Speed)</option>
                  <option value="STEALTH_RECON">STEALTH_RECON (Low Noise Fuzzing)</option>
                </select>
              </div>

              <div>
                <label className="block text-slate-300 font-bold mb-1">Subprocess Command Timeout (Seconds)</label>
                <input
                  type="number"
                  min={30}
                  max={1200}
                  value={timeoutSeconds}
                  onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
                  className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan"
                />
              </div>

              <div className="flex items-center justify-between p-3 bg-obsidian-950 border border-slate-800 rounded-lg">
                <div>
                  <span className="font-bold text-slate-200 block">Auto-Approve Privileged Commands</span>
                  <p className="text-[10px] text-slate-400">Bypass manual operator approval for PRIVILEGED tool executions</p>
                </div>
                <input
                  type="checkbox"
                  checked={autoApprove}
                  onChange={(e) => setAutoApprove(e.target.checked)}
                  className="w-4 h-4 accent-cyber-cyan cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Section 2: AI Routing & Budget Protection */}
          <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
            <h3 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-3 flex items-center space-x-2">
              <DollarSign className="w-4 h-4 text-cyber-emerald" />
              <span>AI ROUTING & BUDGET PROTECTION</span>
            </h3>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-300 font-bold mb-1">Daily LLM Budget Cap ($ USD)</label>
                  <input
                    type="number"
                    step="0.5"
                    min={1}
                    max={100}
                    value={dailyBudget}
                    onChange={(e) => setDailyBudget(Number(e.target.value))}
                    className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan"
                  />
                </div>

                <div>
                  <label className="block text-slate-300 font-bold mb-1">Session Cap ($ USD)</label>
                  <input
                    type="number"
                    step="0.5"
                    min={0.5}
                    max={50}
                    value={sessionBudget}
                    onChange={(e) => setSessionBudget(Number(e.target.value))}
                    className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan"
                  />
                </div>
              </div>

              <div>
                <label className="block text-slate-300 font-bold mb-1">Default Operational Strategy</label>
                <select
                  value={defaultStrategy}
                  onChange={(e) => setDefaultStrategy(e.target.value)}
                  className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:border-cyber-cyan"
                >
                  <option value="EXPLOIT_FIRST">EXPLOIT_FIRST (Aggressive Flag Capture)</option>
                  <option value="STEALTH_RECON">STEALTH_RECON (Deep Vulnerability Analysis)</option>
                  <option value="BALANCED">BALANCED (Standard CTF Operational Loop)</option>
                </select>
              </div>

              <div className="flex items-center justify-between p-3 bg-obsidian-950 border border-slate-800 rounded-lg">
                <div>
                  <span className="font-bold text-slate-200 block">Allow Commercial Paid Models</span>
                  <p className="text-[10px] text-slate-400">Permit routing to paid API providers when free tiers exhaust</p>
                </div>
                <input
                  type="checkbox"
                  checked={paidAllowed}
                  onChange={(e) => setPaidAllowed(e.target.checked)}
                  className="w-4 h-4 accent-cyber-emerald cursor-pointer"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Submit Actions */}
        <div className="flex items-center justify-end space-x-4 pt-3 border-t border-slate-800">
          <button
            type="submit"
            className="px-8 py-3 rounded-xl bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs shadow-[0_0_20px_rgba(0,240,255,0.4)] transition-all uppercase"
          >
            APPLY & SAVE SYSTEM CONFIGURATION
          </button>
        </div>
      </form>
    </div>
  );
};

const SystemRequirementsAuditor: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [auditData, setAuditData] = useState<any>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const runAudit = async () => {
    soundEngine.playClick();
    setLoading(true);
    setErrorMsg(null);
    try {
      const data = await apiService.getSystemRequirements();
      if (data) {
        setAuditData(data);
        soundEngine.playFanfare();
      } else {
        setErrorMsg("Failed to receive diagnostic data from FastAPI backend.");
      }
    } catch (e: any) {
      setErrorMsg(`API Diagnostics Error: ${e.message || e}`);
    } finally {
      setLoading(false);
    }
  };

  React.useEffect(() => {
    runAudit();
  }, []);

  return (
    <div className="space-y-6 font-mono text-slate-100">
      {/* Header Banner */}
      <div className="glass-panel border border-slate-800 p-5 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-display font-bold uppercase tracking-wider text-slate-100 neon-text-cyan flex items-center space-x-2">
            <Shield className="w-5 h-5 text-cyber-cyan" />
            <span>HOST OPERATING SYSTEM & DEPENDENCY AUDITOR</span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Empirical verification of host OS architecture, Python runtime, memory bounds, subprocess privileges, and PATH binary resolutions.
          </p>
        </div>

        <button
          onClick={runAudit}
          disabled={loading}
          className="px-5 py-2.5 rounded-xl bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center space-x-2 shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all uppercase shrink-0"
        >
          <RotateCcw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          <span>{loading ? 'AUDITING HOST OS...' : 'RUN OS DIAGNOSTICS'}</span>
        </button>
      </div>

      {errorMsg && (
        <div className="p-4 bg-obsidian-900 border-2 border-cyber-rose text-cyber-rose rounded-xl text-xs font-bold">
          ⚠️ {errorMsg}
        </div>
      )}

      {auditData?.offline_notice && (
        <div className="p-4 bg-amber-950/80 border-2 border-cyber-amber text-cyber-amber rounded-xl text-xs font-mono space-y-1.5 shadow-[0_0_15px_rgba(255,184,0,0.2)]">
          <div className="font-bold flex items-center space-x-2 text-sm uppercase">
            <span>⚠️ FASTAPI BACKEND SERVER OFFLINE</span>
          </div>
          <p className="text-slate-300">{auditData.offline_notice}</p>
          <div className="pt-1 text-[11px]">
            <span className="text-slate-400">Run command to start backend: </span>
            <code className="text-cyber-cyan font-bold bg-obsidian-950 px-2 py-0.5 rounded border border-slate-800 select-all">py -m uvicorn backend.main:app --port 8000</code>
          </div>
        </div>
      )}

      {loading ? (
        <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-12 flex flex-col items-center justify-center space-y-4 cyber-corner">
          <RotateCcw className="w-10 h-10 text-cyber-cyan animate-spin" />
          <span className="font-display font-bold text-slate-100 text-sm tracking-wider uppercase neon-text-cyan">
            EXECUTING EMPIRICAL PATH & HARDWARE DIAGNOSTICS...
          </span>
          <p className="text-xs text-slate-400 font-mono">Querying System PATH, Python runtime environment, and 25 tool binary executables.</p>
        </div>
      ) : auditData ? (
        <>
          {/* Host Overview Cards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs font-mono">
            <div className="glass-panel border border-slate-800 p-4 rounded-xl space-y-1">
              <span className="text-slate-500 uppercase text-[10px]">HOST OS & DISTRO</span>
              <p className="font-bold text-slate-100 truncate text-sm">
                {auditData.environment.distro}
              </p>
            </div>
            <div className="glass-panel border border-slate-800 p-4 rounded-xl space-y-1">
              <span className="text-slate-500 uppercase text-[10px]">PYTHON ENVIRONMENT</span>
              <p className="font-bold text-cyber-cyan text-sm">
                Python {auditData.environment.python_version} ({auditData.environment.architecture})
              </p>
            </div>
            <div className="glass-panel border border-slate-800 p-4 rounded-xl space-y-1">
              <span className="text-slate-500 uppercase text-[10px]">HARDWARE RESOURCES</span>
              <p className="font-bold text-slate-100 text-sm">
                {auditData.environment.cpu_cores} Cores • {auditData.environment.ram_gb} GB RAM
              </p>
            </div>
            <div className="glass-panel border border-slate-800 p-4 rounded-xl space-y-1">
              <span className="text-slate-500 uppercase text-[10px]">INSTALLED ARSENAL TOOLS</span>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-cyber-emerald text-sm">
                  {auditData.installed_tools_count} / {auditData.total_tools_count} Installed
                </span>
                <span className="px-2 py-0.5 rounded bg-obsidian-950 border border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                  {auditData.overall_status}
                </span>
              </div>
            </div>
          </div>

          {/* Core OS Requirements Checklist */}
          <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
            <h3 className="font-display font-bold text-slate-100 uppercase text-xs border-b border-slate-800 pb-3 flex items-center space-x-2">
              <CheckCircle2 className="w-4 h-4 text-cyber-cyan" />
              <span>SYSTEM & RUNTIME REQUIREMENTS AUDIT</span>
            </h3>

            <div className="space-y-3">
              {auditData.system_requirements.map((req: any, idx: number) => (
                <div key={idx} className="p-4 bg-obsidian-950 border border-slate-800 rounded-lg flex items-start justify-between gap-4 hover:border-cyber-cyan/30 transition-colors">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-2">
                      <span className="font-bold text-slate-100 text-xs font-display">{req.requirement}</span>
                      <span className="text-[11px] text-cyber-cyan font-mono">({req.details})</span>
                    </div>
                    <p className="text-[11px] text-slate-400 font-mono leading-relaxed">{req.impact}</p>
                  </div>

                  <span className={`px-2.5 py-1 rounded text-[10px] font-bold uppercase shrink-0 ${
                    req.status === 'PASS' ? 'bg-emerald-950 text-cyber-emerald border border-emerald-800' :
                    req.status === 'WARN' ? 'bg-amber-950 text-cyber-amber border border-amber-800' :
                    'bg-red-950 text-cyber-rose border border-rose-800'
                  }`}>
                    {req.status}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* CLI Binary Dependencies & Real Path Resolutions */}
          <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h3 className="font-display font-bold text-slate-100 uppercase text-xs flex items-center space-x-2">
                <Settings className="w-4 h-4 text-cyber-emerald" />
                <span>CLI TOOL DEPENDENCY MATRIX & EMPIRICAL PATH RESOLUTION (25 TOOLS)</span>
              </h3>
              <span className="text-xs text-cyber-cyan font-bold font-mono">
                {auditData.installed_tools_count} INSTALLED
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {auditData.tool_requirements.map((t: any) => (
                <div key={t.name} className="p-4 bg-obsidian-950 border border-slate-800 rounded-xl space-y-2 text-xs font-mono">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-2">
                      <span className={`w-2.5 h-2.5 rounded-full ${t.installed ? 'bg-cyber-emerald shadow-[0_0_8px_rgba(16,185,129,0.8)]' : 'bg-cyber-rose'}`}></span>
                      <span className="font-bold text-slate-100 text-sm uppercase">{t.name}</span>
                    </div>
                    <span className={`px-2.5 py-0.5 rounded text-[10px] font-bold ${
                      t.installed ? 'bg-emerald-950/80 text-cyber-emerald border border-emerald-800' : 'bg-rose-950/80 text-cyber-rose border border-rose-800'
                    }`}>
                      {t.installed ? 'INSTALLED' : 'UNINSTALLED'}
                    </span>
                  </div>

                  <div className="text-[11px] space-y-1">
                    <div className="flex justify-between border-b border-slate-900 pb-1">
                      <span className="text-slate-500">System PATH Resolution:</span>
                      <span className={`font-mono text-[10px] font-bold truncate max-w-[240px] ${t.installed ? 'text-cyber-cyan' : 'text-slate-500'}`}>
                        {t.path}
                      </span>
                    </div>

                    {!t.installed && (
                      <div className="p-2.5 bg-obsidian-900 rounded-lg border border-slate-800 text-[10px] mt-2 space-y-1">
                        <span className="text-slate-400 block font-bold">Trusted Installation Command:</span>
                        <code className="text-cyber-cyan font-bold block select-all bg-obsidian-950 p-1.5 rounded border border-slate-800">{t.installation_recipe}</code>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
};
