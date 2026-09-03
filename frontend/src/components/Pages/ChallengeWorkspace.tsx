import React, { useState } from 'react';
import { 
  Play, 
  Pause, 
  ChevronLeft, 
  Copy, 
  Check, 
  Sparkles,
  ArrowRight
} from 'lucide-react';
import { Challenge, Target, EvidenceItem, AiDecision, TerminalLog, Finding, WorkflowNode } from '../../types';

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

  // Writeup content
  const [writeupText, setWriteupText] = useState<string>(`# CTF WRITEUP: ${challenge.name} (${challenge.category})

## Challenge Information
- **Category**: ${challenge.category}
- **Target IP**: ${target.currentIp} (${target.hostname})
- **Difficulty**: ${challenge.difficulty}
- **Status**: ${challenge.status}
- **Flag**: ${challenge.flag || 'HTB{sql_1nj3ct10n_byp4ss_m4st3r}'}

## 1. Reconnaissance & Target Profiling
Nmap scan revealed active services:
- Port 22/tcp: OpenSSH 8.9p1
- Port 80/tcp: nginx 1.18.0
- Port 8080/tcp: Apache Tomcat 9.0.65

## 2. Attack Surface Enumeration
Fuzzing HTTP endpoints with \`ffuf\` identified authentication route \`/api/v1/auth\`.

## 3. Vulnerability Exploitation
Discovered error-based SQL Injection on parameter \`username\`.
Payload: \`{"username": "admin' OR 1=1--", "password": "x"}\`
Extracted administrative JWT session token.

## 4. Flag Extraction
Root flag obtained from target filesystem.

## 5. Lessons Learned
Parameterize all SQL database queries and enforce strict input validation schemas.
`);

  const handleCopyWriteup = () => {
    navigator.clipboard.writeText(writeupText);
    setCopiedReadme(true);
    setTimeout(() => setCopiedReadme(false), 2000);
  };

  const handleCopyText = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleGenerateWriteup = () => {
    setWriteupText(`[+] AUTOMATICALLY GENERATED CTF WRITEUP FOR ${challenge.name}\n\n# Target: ${target.currentIp}\n# Exploited Endpoint: /api/v1/auth\n# Findings: ${findings.map(f => f.title).join(', ')}\n# Verified Flag: ${challenge.flag || 'HTB{sql_1nj3ct10n_byp4ss_m4st3r}'}`);
  };

  return (
    <div className="space-y-4 font-mono text-slate-100 pb-8">
      {/* Workspace Top Header Bar */}
      <div className="bg-[#0b1019] border border-cyan-500/40 rounded-lg p-4 space-y-3 shadow-[0_0_20px_rgba(6,182,212,0.1)]">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div className="flex items-center space-x-3">
            <button
              onClick={onBackToChallenges}
              className="p-1.5 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>

            <div>
              <div className="flex items-center space-x-2">
                <h1 className="text-xl font-bold tracking-wider text-slate-100">{challenge.name}</h1>
                <span className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-800 text-cyan-400 text-xs font-bold uppercase">
                  {challenge.category} CTF
                </span>
                <span className="text-xs text-slate-400">• TARGET: <span className="text-cyan-300 font-bold">{target.currentIp}</span></span>
              </div>
            </div>
          </div>

          {/* Action Controls & Progress */}
          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-2 text-xs">
              <span className={`w-2.5 h-2.5 rounded-full ${challenge.status === 'RUNNING' ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`}></span>
              <span className="font-bold text-emerald-400">● {challenge.status}</span>
            </div>

            <button
              onClick={() => onToggleStatus(challenge.id)}
              className={`px-3 py-1.5 rounded text-xs font-bold flex items-center space-x-1.5 border ${
                challenge.status === 'RUNNING'
                  ? 'bg-amber-950/80 border-amber-700 text-amber-300 hover:bg-amber-900'
                  : 'bg-emerald-950/80 border-emerald-700 text-emerald-300 hover:bg-emerald-900'
              }`}
            >
              {challenge.status === 'RUNNING' ? (
                <>
                  <Pause className="w-3.5 h-3.5" />
                  <span>PAUSE OPERATION</span>
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5" />
                  <span>RESUME RUN</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Progress Bar Row */}
        <div className="flex items-center space-x-3 text-xs">
          <span className="text-slate-400 shrink-0">CHALLENGE COMPLETION:</span>
          <div className="flex-1 h-2 bg-slate-900 rounded-full overflow-hidden border border-slate-800">
            <div
              className="h-full bg-cyan-400 transition-all duration-300 shadow-[0_0_8px_rgba(6,182,212,0.6)]"
              style={{ width: `${challenge.progress}%` }}
            ></div>
          </div>
          <span className="text-cyan-400 font-bold shrink-0">{challenge.progress}%</span>
        </div>
      </div>

      {/* 7 Workspace Navigation Tabs */}
      <div className="flex items-center space-x-1 border-b border-slate-800 pb-1 overflow-x-auto text-xs font-mono">
        {[
          { key: 'overview', label: 'OVERVIEW' },
          { key: 'workflow', label: 'WORKFLOW PIPELINE' },
          { key: 'terminal', label: 'TERMINAL EXECUTION' },
          { key: 'ai_decisions', label: 'AI DECISIONS' },
          { key: 'evidence', label: 'EVIDENCE' },
          { key: 'findings', label: 'FINDINGS' },
          { key: 'readme', label: 'README / WRITEUP' }
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key as any)}
            className={`px-3 py-1.5 rounded text-xs font-bold transition-all uppercase whitespace-nowrap ${
              activeTab === t.key
                ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/40 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            [ {t.label} ]
          </button>
        ))}
      </div>

      {/* TAB 1: OVERVIEW */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 font-mono text-xs">
          <div className="lg:col-span-2 space-y-4">
            <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg space-y-3">
              <h2 className="font-bold text-slate-100 uppercase border-b border-slate-800 pb-2">CHALLENGE OBJECTIVES & HYPOTHESES</h2>
              <div className="space-y-2">
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase">Current Objective:</span>
                  <span className="text-cyan-300 font-bold">Enumerating HTTP attack surface & verifying SQL injection</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase">Active Hypothesis:</span>
                  <span className="text-slate-200">The /api/v1/auth parameter 'username' lacks input sanitization allowing query injection.</span>
                </div>
              </div>
            </div>

            <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg space-y-3">
              <h2 className="font-bold text-slate-100 uppercase border-b border-slate-800 pb-2">TARGET DISCOVERY & SERVICES</h2>
              <div className="grid grid-cols-2 gap-3 text-[11px]">
                <div>
                  <span className="text-slate-500 block">IP / Host:</span>
                  <span className="text-cyan-300 font-bold">{target.currentIp} ({target.hostname})</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Discovery Method:</span>
                  <span className="text-slate-200">{target.discoveryMethod}</span>
                </div>
              </div>
              <div className="space-y-1 pt-2">
                <span className="text-slate-500 text-[10px] uppercase block">Open Services:</span>
                {target.services.map((s) => (
                  <div key={s.port} className="flex justify-between px-2 py-1 bg-[#070b12] border border-slate-800 rounded">
                    <span className="text-cyan-400 font-bold">{s.port}/{s.proto}</span>
                    <span className="text-slate-200">{s.service}</span>
                    <span className="text-slate-400">{s.version}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg space-y-3">
            <h2 className="font-bold text-slate-100 uppercase border-b border-slate-800 pb-2">LIVE AGENT TELEMETRY</h2>
            <div className="space-y-2">
              <div className="p-2.5 rounded bg-[#070b12] border border-slate-800">
                <div className="flex justify-between font-bold text-cyan-300">
                  <span>ORCHESTRATOR</span>
                  <span className="text-emerald-400">RUNNING</span>
                </div>
                <p className="text-[11px] text-slate-400 mt-1">Coordinating challenge workflow</p>
              </div>

              <div className="p-2.5 rounded bg-[#070b12] border border-slate-800">
                <div className="flex justify-between font-bold text-cyan-300">
                  <span>WEB AGENT</span>
                  <span className="text-cyan-400">ANALYZING</span>
                </div>
                <p className="text-[11px] text-slate-400 mt-1">Fuzzing authentication API</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: WORKFLOW PIPELINE */}
      {activeTab === 'workflow' && (
        <div className="space-y-5 font-mono text-xs">
          <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg">
            <h2 className="font-bold text-slate-100 uppercase tracking-wider mb-1">VISUAL INVESTIGATION PIPELINE</h2>
            <p className="text-slate-400">Interactive CTF attack workflow diagram. Click a node to reveal findings & evidence.</p>
          </div>

          {/* Node Flow Horizontal Pipeline */}
          <div className="bg-[#05080e] border border-slate-800 rounded-lg p-5 overflow-x-auto">
            <div className="flex items-center space-x-2 min-w-max">
              {workflowNodes.map((node, idx) => (
                <React.Fragment key={node.id}>
                  <div
                    onClick={() => setSelectedWorkflowNode(node)}
                    className={`p-3 rounded-lg border cursor-pointer transition-all ${
                      selectedWorkflowNode?.id === node.id
                        ? 'bg-cyan-950/60 border-cyan-400 shadow-[0_0_15px_rgba(6,182,212,0.3)]'
                        : node.status === 'COMPLETED'
                        ? 'bg-emerald-950/30 border-emerald-700/60 text-slate-200'
                        : node.status === 'ACTIVE'
                        ? 'bg-cyan-950/30 border-cyan-500 text-cyan-300'
                        : 'bg-slate-900/40 border-slate-800 text-slate-500'
                    }`}
                  >
                    <div className="flex items-center space-x-2 mb-1">
                      <span className={`w-2 h-2 rounded-full ${
                        node.status === 'COMPLETED' ? 'bg-emerald-400' :
                        node.status === 'ACTIVE' ? 'bg-cyan-400 animate-pulse' :
                        'bg-slate-600'
                      }`}></span>
                      <span className="font-bold text-[11px]">{node.label}</span>
                    </div>
                    <span className="text-[9px] px-1.5 py-0.2 rounded font-bold uppercase block text-center border border-slate-800">
                      {node.status}
                    </span>
                  </div>

                  {idx < workflowNodes.length - 1 && (
                    <ArrowRight className="w-4 h-4 text-slate-600 shrink-0" />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>

          {/* Node Drawer Detail */}
          {selectedWorkflowNode && (
            <div className="bg-[#0b1019] border border-cyan-500/40 rounded-lg p-4 space-y-2">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyan-300 text-sm">{selectedWorkflowNode.label} NODE METADATA</span>
                <span className="px-2 py-0.5 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 text-[10px] font-bold">
                  {selectedWorkflowNode.status}
                </span>
              </div>
              <p className="text-slate-300 text-xs">{selectedWorkflowNode.description}</p>
            </div>
          )}
        </div>
      )}

      {/* TAB 3: TERMINAL */}
      {activeTab === 'terminal' && (
        <div className="bg-[#05080e] border border-slate-800 rounded-lg p-4 font-mono text-xs space-y-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <span className="font-bold text-cyan-300">TERMINAL TELEMETRY FOR {challenge.name}</span>
            <span className="text-[10px] text-slate-400">{logs.length} COMMAND EXECUTIONS</span>
          </div>

          <div className="space-y-3">
            {logs.map((l) => (
              <div key={l.id} className="p-3 bg-[#080d17] border border-slate-900 rounded space-y-1">
                <div className="flex justify-between text-[10px] text-slate-400">
                  <span>[{l.timestamp}] {l.type}</span>
                  <span>EXIT: {l.exitCode} • DURATION: {l.duration}</span>
                </div>
                <div className="text-cyan-300 font-bold">forge@parrot:~$ {l.command}</div>
                <pre className="text-slate-300 text-[11px] whitespace-pre-wrap">{l.output}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 4: AI DECISIONS */}
      {activeTab === 'ai_decisions' && (
        <div className="space-y-3 font-mono text-xs">
          {decisions.map((d) => (
            <div key={d.id} className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-2">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyan-300">{d.agent} • {d.goal}</span>
                <span className="text-emerald-400 font-bold">{d.confidence}% CONFIDENCE</span>
              </div>
              <p className="text-slate-300"><span className="text-slate-500">Capability:</span> {d.capability} → <code className="text-slate-100">{d.selectedTool}</code></p>
              <p className="text-slate-300 bg-[#070b12] p-2 rounded border border-slate-900">{d.result}</p>
            </div>
          ))}
        </div>
      )}

      {/* TAB 5: EVIDENCE */}
      {activeTab === 'evidence' && (
        <div className="space-y-3 font-mono text-xs">
          {evidenceList.map((e) => (
            <div key={e.id} className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-2">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-cyan-300">{e.type} • {e.description}</span>
                <span className="text-slate-400">{e.timestamp}</span>
              </div>
              <pre className="bg-[#05080e] p-3 rounded text-slate-300 overflow-x-auto whitespace-pre-wrap">{e.content}</pre>
              <div className="flex justify-end space-x-2 pt-1">
                <button onClick={() => handleCopyText(e.id, e.content)} className="px-2 py-1 rounded bg-slate-900 border border-slate-700 text-slate-300 text-[10px] font-bold">
                  {copiedId === e.id ? 'COPIED' : 'COPY EVIDENCE'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* TAB 6: FINDINGS */}
      {activeTab === 'findings' && (
        <div className="space-y-3 font-mono text-xs">
          {findings.map((f) => (
            <div key={f.id} className="bg-[#0b1019] border border-red-500/40 rounded-lg p-4 space-y-2 shadow-[0_0_12px_rgba(239,68,68,0.1)]">
              <div className="flex justify-between border-b border-slate-800 pb-2">
                <span className="font-bold text-red-400 text-sm">{f.severity}: {f.title}</span>
                <span className="px-2 py-0.5 rounded bg-emerald-950 border border-emerald-800 text-emerald-400 font-bold">{f.status}</span>
              </div>
              <p className="text-slate-300"><span className="text-slate-500">Endpoint:</span> <code className="text-cyan-300">{f.endpoint}</code></p>
              <p className="text-slate-300 leading-relaxed">{f.description}</p>
            </div>
          ))}
        </div>
      )}

      {/* TAB 7: README / WRITEUP */}
      {activeTab === 'readme' && (
        <div className="bg-[#0b1019] border border-slate-800 rounded-lg p-5 space-y-4 font-mono text-xs">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
            <h2 className="font-bold text-slate-100 uppercase tracking-wider">CTF WRITEUP EDITOR & MARKDOWN GENERATOR</h2>
            <div className="flex items-center space-x-2">
              <button
                onClick={handleGenerateWriteup}
                className="px-3 py-1.5 rounded bg-cyan-950 hover:bg-cyan-900 border border-cyan-700 text-cyan-300 font-bold flex items-center space-x-1.5"
              >
                <Sparkles className="w-3.5 h-3.5" />
                <span>GENERATE WRITEUP</span>
              </button>
              <button
                onClick={handleCopyWriteup}
                className="px-3 py-1.5 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 font-bold flex items-center space-x-1.5"
              >
                {copiedReadme ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copiedReadme ? 'COPIED' : 'COPY MD'}</span>
              </button>
            </div>
          </div>

          <textarea
            rows={16}
            value={writeupText}
            onChange={(e) => setWriteupText(e.target.value)}
            className="w-full bg-[#05080e] border border-slate-800 rounded p-4 text-slate-200 font-mono text-xs focus:outline-none focus:border-cyan-500 leading-relaxed"
          ></textarea>
        </div>
      )}
    </div>
  );
};
