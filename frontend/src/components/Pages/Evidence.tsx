import React, { useState } from 'react';
import { 
  FileText, 
  Search, 
  Copy, 
  Eye, 
  Check,
  Download,
  Database,
  Image as ImageIcon
} from 'lucide-react';
import { EvidenceItem } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

interface EvidenceProps {
  evidence: EvidenceItem[];
}

export const Evidence: React.FC<EvidenceProps> = ({ evidence }) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [typeFilter, setTypeFilter] = useState('ALL');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [selectedItem, setSelectedItem] = useState<EvidenceItem | null>(null);
  const [exportNotice, setExportNotice] = useState<string | null>(null);

  const handleCopy = (id: string, text: string) => {
    soundEngine.playClick();
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleExportAll = () => {
    soundEngine.playFanfare();
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(evidence, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute("download", `FORGE_EVIDENCE_VAULT_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();

    setExportNotice("Evidence vault exported to JSON report cleanly!");
    setTimeout(() => setExportNotice(null), 3000);
  };

  const filtered = evidence.filter((e) => {
    const matchesSearch = e.description.toLowerCase().includes(searchTerm.toLowerCase()) || 
                          (e.target && e.target.includes(searchTerm)) ||
                          (e.tool && e.tool.toLowerCase().includes(searchTerm.toLowerCase()));
    const matchesType = typeFilter === 'ALL' || e.type === typeFilter;
    return matchesSearch && matchesType;
  });

  return (
    <div className="space-y-6 font-mono text-slate-100 pb-10">
      {/* Search & Filter Header */}
      <div className="glass-panel border border-slate-800 p-5 rounded-xl flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4">
        <div className="flex items-center space-x-3 flex-1 flex-wrap gap-y-2">
          <div className="relative flex-1 max-w-sm">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search evidence vault by description, target, tool..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-obsidian-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyber-cyan transition-colors"
            />
          </div>

          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="bg-obsidian-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-cyber-cyan uppercase font-mono"
          >
            <option value="ALL">ALL TYPES</option>
            <option value="HTTP RESPONSE">HTTP RESPONSE</option>
            <option value="COMMAND OUTPUT">COMMAND OUTPUT</option>
            <option value="FINDING">FINDING</option>
            <option value="SCREENSHOT">SCREENSHOT / MD REPORT</option>
            <option value="FILE">FILE</option>
            <option value="HASH">HASH</option>
            <option value="FLAG">FLAG</option>
          </select>
        </div>

        <div className="flex items-center space-x-3">
          <button
            onClick={handleExportAll}
            className="px-4 py-1.5 rounded-lg bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold text-xs flex items-center space-x-1.5 shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all uppercase"
          >
            <Download className="w-3.5 h-3.5" />
            <span>EXPORT VAULT JSON/MD</span>
          </button>
          <span className="text-xs text-cyber-cyan font-bold px-3 py-1.5 rounded-lg bg-obsidian-950 border border-cyber-cyan/40">
            {filtered.length} EVIDENCE ITEMS
          </span>
        </div>
      </div>

      {exportNotice && (
        <div className="p-3 bg-obsidian-900 border border-cyber-emerald text-cyber-emerald rounded-lg text-xs font-bold flex items-center space-x-2 animate-bounce">
          <Check className="w-4 h-4 text-cyber-emerald" />
          <span>{exportNotice}</span>
        </div>
      )}

      {/* IF NO EVIDENCE ITEMS IN REPOSITORY */}
      {(!evidence || evidence.length === 0) ? (
        <div className="glass-panel border-2 border-cyber-cyan/40 rounded-xl p-10 flex flex-col items-center justify-center text-center space-y-4 cyber-corner">
          <div className="w-16 h-16 rounded-2xl bg-obsidian-900 border border-cyber-cyan flex items-center justify-center text-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.4)] animate-pulse">
            <Database className="w-8 h-8" />
          </div>
          <div className="max-w-md space-y-2">
            <h2 className="text-xl font-display font-bold text-slate-100 uppercase neon-text-cyan">
              EVIDENCE VAULT STANDBY
            </h2>
            <p className="text-xs text-slate-400 font-mono leading-relaxed">
              The Evidence Vault stores all HTTP response payloads, command outputs, screenshots, flag captures, and writeup Markdown reports generated during operations. Start a challenge to begin populating evidence.
            </p>
          </div>
        </div>
      ) : (
        /* Evidence Cards List */
        <div className="space-y-4">
          {filtered.map((item) => (
            <div key={item.id} className="glass-panel border border-slate-800 rounded-xl p-5 space-y-3 font-mono text-xs hover:border-cyber-cyan/40 transition-colors">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800 pb-3">
                <div className="flex items-center space-x-3">
                  <span className={`px-2.5 py-1 rounded text-[10px] font-bold uppercase border ${
                    item.type === 'FINDING' ? 'bg-red-950 text-cyber-rose border-cyber-rose/60' :
                    item.type === 'FLAG' ? 'bg-emerald-950 text-cyber-emerald border-emerald-800' :
                    'bg-cyan-950 text-cyber-cyan border-cyber-cyan/50'
                  }`}>
                    {item.type}
                  </span>
                  <span className="font-bold text-slate-100 text-sm font-display">{item.description}</span>
                </div>

                <div className="flex items-center space-x-3 text-[11px] text-slate-400">
                  <span>TARGET: <span className="text-cyber-cyan font-bold">{item.target}</span></span>
                  <span>AGENT: <span className="text-slate-200 font-bold">{item.agent}</span></span>
                  <span>TOOL: <span className="text-slate-200 font-bold">{item.tool}</span></span>
                  <span>{item.timestamp}</span>
                </div>
              </div>

              {/* Content Preview Box */}
              <div className="bg-obsidian-950 border border-slate-900 rounded-lg p-4 text-slate-300 text-xs overflow-x-auto max-h-36 font-mono leading-relaxed whitespace-pre-wrap">
                {item.type === 'SCREENSHOT' ? (
                  <div className="flex items-center space-x-2 text-cyber-cyan">
                    <ImageIcon className="w-4 h-4" />
                    <span>[SCREENSHOT ARTIFACT CAPTURED] — {item.content}</span>
                  </div>
                ) : (
                  item.content
                )}
              </div>

              {/* Actions Toolbar */}
              <div className="flex items-center justify-between pt-3 border-t border-slate-800">
                <span className="text-[10px] text-slate-500 font-bold">SOURCE: {item.source}</span>

                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => { soundEngine.playClick(); setSelectedItem(item); }}
                    className="px-3 py-1.5 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-200 text-[11px] font-bold flex items-center space-x-1.5 transition-colors"
                  >
                    <Eye className="w-3.5 h-3.5 text-cyber-cyan" />
                    <span>PREVIEW & READ MD</span>
                  </button>

                  <button
                    onClick={() => handleCopy(item.id, item.content)}
                    className="px-3 py-1.5 rounded-lg bg-obsidian-900 hover:bg-slate-800 border border-slate-700 text-slate-200 text-[11px] font-bold flex items-center space-x-1.5 transition-colors"
                  >
                    {copiedId === item.id ? <Check className="w-3.5 h-3.5 text-cyber-emerald" /> : <Copy className="w-3.5 h-3.5 text-slate-400" />}
                    <span>{copiedId === item.id ? 'COPIED' : 'COPY'}</span>
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modal Inspector for Full Content View */}
      {selectedItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-obsidian-950/80 backdrop-blur-md p-4">
          <div className="w-full max-w-3xl glass-panel border-2 border-cyber-cyan/60 rounded-xl p-6 space-y-4 font-mono text-xs shadow-[0_0_40px_rgba(0,240,255,0.25)] cyber-corner">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-2">
                <FileText className="w-5 h-5 text-cyber-cyan" />
                <span className="font-bold text-slate-100 text-sm font-display">{selectedItem.description}</span>
              </div>
              <button onClick={() => setSelectedItem(null)} className="text-slate-400 hover:text-slate-100 font-bold">
                ✕ CLOSE
              </button>
            </div>
            <pre className="bg-obsidian-950 p-5 rounded-lg border border-slate-900 text-slate-200 overflow-auto max-h-[450px] whitespace-pre-wrap leading-relaxed">
              {selectedItem.content}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

