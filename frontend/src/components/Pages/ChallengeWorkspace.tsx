import React, { useState } from 'react';
import { 
  Play, 
  Pause, 
  ChevronLeft, 
  Copy, 
  Check, 
  Sparkles,
  ArrowRight,
  Shield,
  Layers,
  Terminal,
  Cpu,
  FileText,
  AlertTriangle,
  FileCode,
  Folder
} from 'lucide-react';
import { Challenge, Target, EvidenceItem, AiDecision, TerminalLog, Finding, WorkflowNode } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

interface ChallengeWorkspaceProps {
  challenge: Challenge;
  target: Target;
  evidenceList: EvidenceItem[];
  decisions: AiDecision[];
  logs: TerminalLog[];
  findings: Finding[];
  workflowNodes: WorkflowNode[];
  onBackToChallenges: () => void;
  onToggleStatus: (id: string) => void;
}

export const ChallengeWorkspace: React.FC<ChallengeWorkspaceProps> = ({
  challenge,
  target,
  evidenceList,
  decisions,
  logs,
  findings,
  workflowNodes,
  onBackToChallenges,
  onToggleStatus
}) => {
  const [activeTab, setActiveTab] = useState<
    'overview' | 'workflow' | 'terminal' | 'ai_decisions' | 'evidence' | 'findings' | 'readme'
  >('overview');

  const [selectedWorkflowNode, setSelectedWorkflowNode] = useState<WorkflowNode | null>(workflowNodes[6] || null);
  const [copiedReadme, setCopiedReadme] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const buildDynamicWriteup = () => {
    const evidenceText = evidenceList.map(e => `### [${e.source}] ${e.description}\n\`\`\`text\n${e.content}\n\`\`\``).join('\n\n') || '*No evidence collected yet.*';
    const findingsText = findings.map(f => `- **${f.severity}**: ${f.title} — ${f.description}`).join('\n') || '*No findings logged yet.*';
    return `# CTF WRITEUP: ${challenge.name} (${challenge.category})

## Challenge Information
- **Platform / Competition**: ${challenge.platformName || 'FORGE CTF Framework'}
- **Category**: ${challenge.category}
- **Target Address**: ${target.currentIp} (${target.hostname})
- **Working Directory**: \`${challenge.workingDirectory || './workspaces/' + challenge.name.toLowerCase().replace(/\s+/g, '_')}\`
- **Difficulty**: ${challenge.difficulty}
- **Status**: ${challenge.status}
- **Flag**: ${challenge.flag || (challenge.flagStatus === 'CAPTURED' ? 'FORGE{flag_captured}' : 'Pending / Unfound')}

## 1. Reconnaissance & Investigation Strategy
The FORGE Autonomous Framework conducted targeted analysis on \`${target.currentIp}\`.

## 2. Key Findings & Vulnerability Assessment
${findingsText}

## 3. Collected Telemetry & Evidence Artifacts
${evidenceText}

## 4. Flag Extraction & Verification
- Status: **${challenge.flagStatus}**
- Flag: \`${challenge.flag || 'In Progress'}\`
`;
  };

  // Writeup content state
  const [writeupText, setWriteupText] = useState<string>(buildDynamicWriteup());

  const handleTabChange = (tabKey: any) => {
    soundEngine.playClick();
    setActiveTab(tabKey);
  };

  const handleCopyWriteup = () => {
    soundEngine.playSuccess();
    navigator.clipboard.writeText(writeupText);
    setCopiedReadme(true);
    setTimeout(() => setCopiedReadme(false), 2000);
  };

  const handleCopyText = (id: string, text: string) => {
    soundEngine.playClick();
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleGenerateWriteup = () => {
    soundEngine.playSuccess();
    setWriteupText(buildDynamicWriteup());
  };

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-10">
      {/* Workspace Top Header Bar */}
      <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-5 space-y-4 shadow-[0_0_30px_rgba(0,240,255,0.15)] cyber-corner">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
          <div className="flex items-center space-x-4">
            <button
              onClick={() => { soundEngine.playClick(); onBackToChallenges(); }}
              className="p-2 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-300 transition-all hover:scale-105"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>

            <div>
              <div className="flex items-center space-x-3 flex-wrap">
                <h1 className="text-2xl font-display font-bold tracking-wider text-slate-100 neon-text-cyan">{challenge.name}</h1>
                <span className="px-2.5 py-1 rounded bg-cyan-950/80 border border-cyber-cyan/60 text-cyber-cyan text-xs font-bold uppercase">
                  {challenge.category} CTF
                </span>
                <span className="text-xs text-slate-400">• TARGET: <span className="text-cyber-cyan font-bold">{target.currentIp}</span></span>
              </div>
            </div>
          </div>

          {/* Action Controls & Progress */}
          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-2 text-xs">
              <span className={`w-3 h-3 rounded-full ${challenge.status === 'RUNNING' ? 'bg-cyber-emerald animate-ping' : 'bg-cyber-amber'}`}></span>
              <span className="font-bold text-cyber-emerald tracking-wider">● {challenge.status}</span>
            </div>

            <button
              onClick={() => { soundEngine.playClick(); onToggleStatus(challenge.id); }}
              className={`px-4 py-2 rounded-lg text-xs font-bold flex items-center space-x-2 border transition-all ${
                challenge.status === 'RUNNING'
                  ? 'bg-amber-950/80 border-amber-700 text-cyber-amber hover:bg-amber-900'
                  : 'bg-emerald-950/80 border-emerald-700 text-cyber-emerald hover:bg-emerald-900'
              }`}
            >
              {challenge.status === 'RUNNING' ? (
                <>
                  <Pause className="w-4 h-4" />
                  <span>PAUSE OPERATION</span>
                </>
              ) : (
                <>
                  <Play className="w-4 h-4" />
                  <span>RESUME RUN</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Progress Bar Row */}
        <div className="flex items-center space-x-4 text-xs">
          <span className="text-slate-400 font-bold shrink-0">CHALLENGE COMPLETION:</span>
          <div className="flex-1 h-2.5 bg-obsidian-950 rounded-full overflow-hidden border border-slate-800">
            <div
              className="h-full bg-cyber-cyan transition-all duration-500 shadow-[0_0_12px_#00f0ff]"
              style={{ width: `${challenge.progress}%` }}
            ></div>
          </div>
          <span className="text-cyber-cyan font-bold text-sm shrink-0">{challenge.progress}%</span>
        </div>
      </div>

      {/* 7 Workspace Navigation Tabs */}
      <div className="flex items-center space-x-2 border-b border-slate-800 pb-2 overflow-x-auto text-xs font-mono">
        {[
          { key: 'overview', label: 'OVERVIEW', icon: Shield },
          { key: 'workflow', label: 'PIPELINE GRAPH', icon: Layers },
          { key: 'terminal', label: 'TERMINAL', icon: Terminal },
          { key: 'ai_decisions', label: 'AI REASONING', icon: Cpu },
          { key: 'evidence', label: 'EVIDENCE VAULT', icon: FileText },
          { key: 'findings', label: 'FINDINGS', icon: AlertTriangle },
          { key: 'readme', label: 'WRITEUP MD', icon: FileCode }
        ].map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => handleTabChange(t.key)}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center space-x-2 whitespace-nowrap ${
                activeTab === t.key
                  ? 'bg-cyber-cyan text-obsidian-950 font-display shadow-[0_0_15px_rgba(0,240,255,0.4)]'
                  : 'bg-obsidian-900/60 text-slate-400 hover:text-slate-200 border border-slate-800'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>

      {/* TAB 1: OVERVIEW */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 font-mono text-xs">
          <div className="lg:col-span-2 space-y-5">
            <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
              <h2 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-2 text-sm neon-text-cyan">
                CHALLENGE OBJECTIVES & HYPOTHESES
              </h2>
              <div className="space-y-3">
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block text-[10px] uppercase font-bold mb-1">Current Primary Objective:</span>
                  <span className="text-cyber-cyan font-bold text-sm">Enumerating HTTP attack surface & verifying SQL injection payload</span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block text-[10px] uppercase font-bold mb-1">Active AI Hypothesis:</span>
                  <span className="text-slate-200 leading-relaxed">The /api/v1/auth parameter 'username' lacks input sanitization allowing query injection.</span>
                </div>
              </div>
            </div>

            <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
              <h2 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-2 text-sm neon-text-cyan">
                TARGET DISCOVERY & SERVICES MATRIX
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-[11px]">
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5">IP / Host:</span>
                  <span className="text-cyber-cyan font-bold text-sm">{target.currentIp} ({target.hostname})</span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5">Platform / CTF:</span>
                  <span className="text-purple-300 font-bold text-xs truncate block">{challenge.platformName || 'FORGE CTF'}</span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5 flex items-center space-x-1">
                    <Folder className="w-3.5 h-3.5 text-cyber-cyan" />
                    <span>Working Directory:</span>
                  </span>
                  <span className="text-cyber-cyan font-bold text-xs truncate block" title={challenge.workingDirectory}>
                    {challenge.workingDirectory || './workspaces/' + challenge.name.toLowerCase().replace(/\s+/g, '_')}
                  </span>
                </div>
                <div className="bg-obsidian-950 p-3 rounded-lg border border-slate-800">
                  <span className="text-slate-500 block font-bold mb-0.5">Discovery Method:</span>
                  <span className="text-slate-200 font-semibold">{target.discoveryMethod}</span>
                </div>
              </div>
              <div className="space-y-2 pt-2">
                <span className="text-slate-400 text-[10px] font-bold uppercase block">Open Target Ports:</span>
                {target.services.map((s) => (
                  <div key={s.port} className="flex justify-between items-center px-3 py-2 bg-obsidian-950 border border-slate-800 rounded-lg text-xs">
                    <span className="text-cyber-cyan font-bold">{s.port}/{s.proto}</span>
                    <span className="text-slate-200 font-semibold">{s.service}</span>
                    <span className="text-slate-400 text-[11px]">{s.version}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="glass-panel border border-slate-800 p-5 rounded-xl space-y-4">
            <h2 className="font-display font-bold text-slate-100 uppercase border-b border-slate-800 pb-2 text-sm neon-text-cyan">
              ASSIGNED AGENT TELEMETRY
            </h2>
            <div className="space-y-3">
              <div className="p-3.5 rounded-lg bg-obsidian-950 border border-cyber-cyan/40">
                <div className="flex justify-between font-bold text-cyber-cyan">
                  <span>ORCHESTRATOR</span>
                  <span className="text-cyber-emerald">RUNNING</span>
                </div>
                <p className="text-[11px] text-slate-400 mt-1">Coordinating challenge workflow execution</p>
              </div>

              <div className="p-3.5 rounded-lg bg-obsidian-950 border border-cyber-cyan/40">
                <div className="flex justify-between font-bold text-cyber-cyan">
                  <span>WEB AGENT</span>
                  <span className="text-cyber-cyan">ANALYZING</span>
                </div>
                <p className="text-[11px] text-slate-400 mt-1">Fuzzing authentication API endpoint</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: WORKFLOW PIPELINE */}
      {activeTab === 'workflow' && (
        <div className="space-y-6 font-mono text-xs">
          <div className="glass-panel border border-slate-800 p-5 rounded-xl">
            <h2 className="font-display font-bold text-slate-100 uppercase tracking-wider mb-1 text-sm neon-text-cyan">
              VISUAL INVESTIGATION PIPELINE
            </h2>
            <p className="text-slate-400">Interactive CTF attack workflow diagram. Click a node to inspect step telemetry.</p>
          </div>

          {/* Node Flow Horizontal Pipeline */}
          <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-6 overflow-x-auto shadow-inner">
            <div className="flex items-center space-x-3 min-w-max">
              {workflowNodes.map((node, idx) => (
                <React.Fragment key={node.id}>
                  <div
                    onClick={() => { soundEngine.playClick(); setSelectedWorkflowNode(node); }}
                    className={`p-4 rounded-xl border cursor-pointer transition-all hover:scale-105 ${
                      selectedWorkflowNode?.id === node.id
                        ? 'bg-cyber-cyan/20 border-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.4)]'
                        : node.status === 'COMPLETED'
                        ? 'bg-emerald-950/30 border-cyber-emerald/60 text-slate-200'
                        : node.status === 'ACTIVE'
                        ? 'bg-cyan-950/30 border-cyber-cyan text-cyber-cyan'
                        : 'bg-obsidian-900/40 border-slate-800 text-slate-500'
                    }`}
                  >
                    <div className="flex items-center space-x-2 mb-2">
                      <span className={`w-2.5 h-2.5 rounded-full ${
                        node.status === 'COMPLETED' ? 'bg-cyber-emerald' :
                        node.status === 'ACTIVE' ? 'bg-cyber-cyan animate-ping' :
                        'bg-slate-600'
                      }`}></span>
                      <span className="font-bold text-xs font-display">{node.label}</span>
                    </div>
                    <span className="text-[9px] px-2 py-0.5 rounded font-bold uppercase block text-center border border-slate-800 bg-obsidian-950">
                      {node.status}
                    </span>
                  </div>

                  {idx < workflowNodes.length - 1 && (
                    <ArrowRight className="w-5 h-5 text-slate-600 shrink-0" />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>

          {/* Node Drawer Detail */}
          {selectedWorkflowNode && (
            <div className="glass-panel border-2 border-cyber-cyan/50 rounded-xl p-5 space-y-3 cyber-corner">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <span className="font-display font-bold text-cyber-cyan text-sm">{selectedWorkflowNode.label} NODE TELEMETRY</span>
                <span className="px-2.5 py-1 rounded bg-emerald-950 border border-emerald-800 text-cyber-emerald text-[10px] font-bold">
                  {selectedWorkflowNode.status}
                </span>
              </div>
              <p className="text-slate-300 text-xs leading-relaxed">{selectedWorkflowNode.description}</p>
            </div>
          )}
        </div>
      )}

      {/* TAB 3: TERMINAL */}
      {activeTab === 'terminal' && (
        <div className="bg-obsidian-950 border border-slate-800 rounded-xl p-5 font-mono text-xs space-y-4 shadow-inner">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <span className="font-display font-bold text-cyber-cyan text-sm">TERMINAL STREAM FOR {challenge.name}</span>
            <span className="text-[10px] text-slate-400 font-bold">{logs.length} COMMANDS LOGGED</span>
          </div>

          <div className="space-y-3 max-h-96 overflow-y-auto">
            {logs.map((l) => (
              <div key={l.id} className="p-3.5 bg-obsidian-900 border border-slate-800/80 rounded-lg space-y-1.5 hover:border-cyber-cyan/30 transition-colors">
                <div className="flex justify-between text-[10px] text-slate-400">
                  <span>[{l.timestamp}] {l.type}</span>
                  <span>EXIT: {l.exitCode} • DURATION: {l.duration}</span>
                </div>
                <div className="text-cyber-cyan font-bold font-mono">forge@parrot:~$ {l.command}</div>
                <pre className="text-slate-300 text-[11px] whitespace-pre-wrap bg-obsidian-950 p-2.5 rounded border border-slate-900">{l.output}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 4: AI DECISIONS */}
      {activeTab === 'ai_decisions' && (
        <div className="space-y-4 font-mono text-xs">
          {decisions.map((d) => (
            <div key={d.id} className="glass-panel border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyber-cyan font-display text-sm">{d.agent} • {d.goal}</span>
                <span className="text-cyber-emerald font-bold">{d.confidence}% CONFIDENCE</span>
              </div>
              <p className="text-slate-300"><span className="text-slate-500 font-bold">Capability:</span> {d.capability} → <code className="text-cyber-cyan font-bold">{d.selectedTool}</code></p>
              <p className="text-slate-300 bg-obsidian-950 p-3 rounded-lg border border-slate-900 leading-relaxed">{d.result}</p>
            </div>
          ))}
        </div>
      )}

      {/* TAB 5: EVIDENCE */}
      {activeTab === 'evidence' && (
        <div className="space-y-4 font-mono text-xs">
          {evidenceList.map((e) => (
            <div key={e.id} className="glass-panel border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyber-cyan font-display text-sm">{e.type} • {e.description}</span>
                <span className="text-slate-400">{e.timestamp}</span>
              </div>
              <pre className="bg-obsidian-950 p-4 rounded-lg text-slate-300 overflow-x-auto whitespace-pre-wrap border border-slate-900">{e.content}</pre>
              <div className="flex justify-end space-x-2 pt-1">
                <button onClick={() => handleCopyText(e.id, e.content)} className="px-3 py-1.5 rounded-lg bg-obsidian-900 border border-slate-700 text-slate-300 hover:text-cyber-cyan text-xs font-bold transition-colors">
                  {copiedId === e.id ? 'COPIED' : 'COPY EVIDENCE'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* TAB 6: FINDINGS */}
      {activeTab === 'findings' && (
        <div className="space-y-4 font-mono text-xs">
          {findings.map((f) => (
            <div key={f.id} className="glass-panel border-2 border-cyber-rose/50 rounded-xl p-5 space-y-3 shadow-[0_0_20px_rgba(255,42,109,0.15)] cyber-corner">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyber-rose text-sm font-display">{f.severity}: {f.title}</span>
                <span className="px-2.5 py-1 rounded bg-emerald-950 border border-emerald-800 text-cyber-emerald font-bold">{f.status}</span>
              </div>
              <p className="text-slate-300"><span className="text-slate-500 font-bold">Endpoint:</span> <code className="text-cyber-cyan font-bold">{f.endpoint}</code></p>
              <p className="text-slate-300 leading-relaxed bg-obsidian-950 p-3 rounded-lg border border-slate-900">{f.description}</p>
            </div>
          ))}
        </div>
      )}

      {/* TAB 7: README / WRITEUP */}
      {activeTab === 'readme' && (
        <div className="glass-panel border border-slate-800 rounded-xl p-6 space-y-4 font-mono text-xs">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
            <h2 className="font-display font-bold text-slate-100 uppercase tracking-wider text-sm neon-text-cyan">
              CTF WRITEUP EDITOR & MARKDOWN GENERATOR
            </h2>
            <div className="flex items-center space-x-2">
              <button
                onClick={handleGenerateWriteup}
                className="px-3.5 py-2 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/30 border border-cyber-cyan/60 text-cyber-cyan font-bold flex items-center space-x-1.5 transition-colors"
              >
                <Sparkles className="w-4 h-4 text-cyber-cyan" />
                <span>AUTO WRITEUP</span>
              </button>
              <button
                onClick={handleCopyWriteup}
                className="px-3.5 py-2 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-200 font-bold flex items-center space-x-1.5 transition-colors"
              >
                {copiedReadme ? <Check className="w-4 h-4 text-cyber-emerald" /> : <Copy className="w-4 h-4" />}
                <span>{copiedReadme ? 'COPIED' : 'COPY MD'}</span>
              </button>
            </div>
          </div>

          <textarea
            rows={16}
            value={writeupText}
            onChange={(e) => setWriteupText(e.target.value)}
            className="w-full bg-obsidian-950 border border-slate-800 rounded-lg p-4 text-slate-200 font-mono text-xs focus:outline-none focus:border-cyber-cyan leading-relaxed shadow-inner"
          ></textarea>
        </div>
      )}
    </div>
  );
};

