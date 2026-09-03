import React, { useState } from 'react';
import { 
  FileText, 
  Search, 
  Copy, 
  Eye, 
  Plus, 
  Check
} from 'lucide-react';
import { EvidenceItem } from '../../types';

interface EvidenceProps {
  evidence: EvidenceItem[];
}

export const Evidence: React.FC<EvidenceProps> = ({ evidence }) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [typeFilter, setTypeFilter] = useState('ALL');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [selectedItem, setSelectedItem] = useState<EvidenceItem | null>(null);

  const handleCopy = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const filtered = evidence.filter((e) => {
    const matchesSearch = e.description.toLowerCase().includes(searchTerm.toLowerCase()) || 
                          e.target.includes(searchTerm) ||
                          e.tool.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesType = typeFilter === 'ALL' || e.type === typeFilter;
    return matchesSearch && matchesType;
  });

  return (
    <div className="space-y-5 font-mono text-slate-100 pb-8">
      {/* Search & Filter Header */}
      <div className="bg-[#0b1019] border border-slate-800 p-4 rounded-lg flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4">
        <div className="flex items-center space-x-3 flex-1">
          <div className="relative flex-1 max-w-sm">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search evidence by description, target, tool..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-[#05080e] border border-slate-800 rounded pl-9 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="bg-[#05080e] border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-cyan-500 uppercase"
          >
            <option value="ALL">ALL TYPES</option>
            <option value="HTTP RESPONSE">HTTP RESPONSE</option>
            <option value="COMMAND OUTPUT">COMMAND OUTPUT</option>
            <option value="FINDING">FINDING</option>
            <option value="SCREENSHOT">SCREENSHOT</option>
            <option value="FILE">FILE</option>
            <option value="HASH">HASH</option>
            <option value="FLAG">FLAG</option>
          </select>
        </div>

        <span className="text-xs text-cyan-400 font-bold px-3 py-1.5 rounded bg-cyan-950 border border-cyan-800">
          {filtered.length} EVIDENCE ITEMS REPOSITORY
        </span>
      </div>

      {/* Evidence Cards List */}
      <div className="space-y-3">
        {filtered.map((item) => (
          <div key={item.id} className="bg-[#0b1019] border border-slate-800 rounded-lg p-4 space-y-3 font-mono text-xs">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800 pb-2.5">
              <div className="flex items-center space-x-3">
                <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${
                  item.type === 'FINDING' ? 'bg-red-950 text-red-400 border-red-800' :
                  item.type === 'FLAG' ? 'bg-emerald-950 text-emerald-400 border-emerald-800' :
                  'bg-cyan-950 text-cyan-400 border-cyan-800'
                }`}>
                  {item.type}
                </span>
                <span className="font-bold text-slate-100">{item.description}</span>
              </div>

              <div className="flex items-center space-x-3 text-[11px] text-slate-400">
                <span>TARGET: <span className="text-cyan-300 font-bold">{item.target}</span></span>
                <span>AGENT: <span className="text-slate-200">{item.agent}</span></span>
                <span>TOOL: <span className="text-slate-200">{item.tool}</span></span>
                <span>{item.timestamp}</span>
              </div>
            </div>

            {/* Content Preview Box */}
            <div className="bg-[#05080e] border border-slate-900 rounded p-3 text-slate-300 text-xs overflow-x-auto max-h-36 font-mono leading-relaxed whitespace-pre-wrap">
              {item.content}
            </div>

            {/* Actions Toolbar */}
            <div className="flex items-center justify-between pt-2 border-t border-slate-800/80">
              <span className="text-[10px] text-slate-500">SOURCE: {item.source}</span>

              <div className="flex items-center space-x-2">
                <button
                  onClick={() => setSelectedItem(item)}
                  className="px-2.5 py-1 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 text-[11px] font-bold flex items-center space-x-1"
                >
                  <Eye className="w-3 h-3" />
                  <span>VIEW</span>
                </button>

                <button
                  onClick={() => handleCopy(item.id, item.content)}
                  className="px-2.5 py-1 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 text-[11px] font-bold flex items-center space-x-1"
                >
                  {copiedId === item.id ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                  <span>{copiedId === item.id ? 'COPIED' : 'COPY'}</span>
                </button>

                <button
                  className="px-2.5 py-1 rounded bg-cyan-950 hover:bg-cyan-900 border border-cyan-700 text-cyan-300 text-[11px] font-bold flex items-center space-x-1"
                >
                  <Plus className="w-3 h-3" />
                  <span>ADD TO FINDING</span>
                </button>

                <button
                  className="px-2.5 py-1 rounded bg-cyan-950 hover:bg-cyan-900 border border-cyan-700 text-cyan-300 text-[11px] font-bold flex items-center space-x-1"
                >
                  <FileText className="w-3 h-3" />
                  <span>ADD TO REPORT</span>
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Modal Inspector for Full Content View */}
      {selectedItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="w-full max-w-2xl bg-[#0d121c] border border-cyan-500/50 rounded-lg p-6 space-y-4 font-mono text-xs shadow-[0_0_40px_rgba(6,182,212,0.2)]">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <span className="font-bold text-cyan-300 text-sm">{selectedItem.description}</span>
              <button onClick={() => setSelectedItem(null)} className="text-slate-400 hover:text-slate-100 font-bold">CLOSE [X]</button>
            </div>
            <pre className="bg-[#05080e] p-4 rounded border border-slate-900 text-slate-200 overflow-auto max-h-96 whitespace-pre-wrap">
              {selectedItem.content}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};
