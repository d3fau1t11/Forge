/**
 * NewChallengeChat — Two-turn conversational challenge creation for FORGE.
 *
 * Turn 1: Free-text collection of Challenge Name, Platform/Event Name, and Challenge Type.
 * Turn 2: Optional Target Address, optional File Attachment(s), and mandatory Goal Description.
 * On commit: Backend commits challenge row, syncs backend data, and routes the operator
 * directly into the Challenge Workspace / pipeline investigation view.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  MessageSquare,
  Paperclip,
  X,
  ArrowLeft,
  Loader2,
  AlertTriangle,
  Send,
  Sparkles,
  Bot,
  User,
  RefreshCw,
  FileCode,
  Globe,
  Terminal,
  Cpu
} from 'lucide-react';
import { apiService } from '../../services/api';
import { Challenge, NavTab } from '../../types';
import { soundEngine } from '../../utils/soundEngine';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatMessage {
  id: string;
  role: 'bot' | 'user';
  text: string;
  timestamp: Date;
  stepState?: 1 | 2 | 'committed';
  meta?: {
    name?: string;
    platform?: string;
    type?: string;
    target?: string;
    description?: string;
    files?: { name: string; size: number }[];
  };
}

interface UploadedFile {
  name: string;
  path: string;
  size: number;
}

interface NewChallengeChatProps {
  onOpenWorkspace: (challenge: Challenge) => void;
  onRefreshBackendData?: () => Promise<void> | void;
  setActiveTab?: (tab: NavTab) => void;
  onClose?: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mkId() {
  return Math.random().toString(36).slice(2, 10);
}

/** Minimal markdown formatter for bot instructions: **bold** and `code` tags */
function renderMarkdown(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={i} className="text-cyber-cyan font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={i} className="bg-obsidian-900 text-cyber-amber px-1.5 py-0.5 rounded text-[11px] border border-amber-500/20">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part.split('\n').map((line, j, arr) => (
      <React.Fragment key={`${i}-${j}`}>
        {line}
        {j < arr.length - 1 && <br />}
      </React.Fragment>
    ));
  });
}

const FIELD_CLS =
  'w-full bg-obsidian-950/90 border border-cyan-500/30 rounded-lg px-3.5 py-2.5 text-xs text-slate-100 ' +
  'placeholder:text-slate-500 focus:outline-none focus:border-cyber-cyan focus:ring-1 focus:ring-cyber-cyan/40 transition-all font-mono';

// ---------------------------------------------------------------------------
// Turn 1 Form: Name, Platform, Type (All free-text)
// ---------------------------------------------------------------------------

const Turn1Form: React.FC<{
  onSubmit: (name: string, platform: string, type: string) => void;
  disabled: boolean;
}> = ({ onSubmit, disabled }) => {
  const [name, setName] = useState('');
  const [platform, setPlatform] = useState('');
  const [type, setType] = useState('');

  const canSubmit = name.trim() && platform.trim() && type.trim() && !disabled;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canSubmit) {
      soundEngine.playClick();
      onSubmit(name.trim(), platform.trim(), type.trim());
    }
  };

  return (
    <form onSubmit={handleSubmit} className="mt-4 pt-3 border-t border-cyan-500/20 space-y-3 font-mono">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="space-y-1">
          <label className="text-[10px] font-bold text-cyber-cyan uppercase tracking-wider flex items-center space-x-1">
            <Cpu className="w-3 h-3 text-cyber-cyan" />
            <span>1. Challenge Name</span>
            <span className="text-cyber-rose">*</span>
          </label>
          <input
            id="chat-challenge-name"
            type="text"
            placeholder="e.g. Impossible Password, Matrix Rev"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={disabled}
            className={FIELD_CLS}
            autoFocus
          />
        </div>

        <div className="space-y-1">
          <label className="text-[10px] font-bold text-cyber-cyan uppercase tracking-wider flex items-center space-x-1">
            <Globe className="w-3 h-3 text-cyber-cyan" />
            <span>2. Platform / Event</span>
            <span className="text-cyber-rose">*</span>
          </label>
          <input
            id="chat-platform-name"
            type="text"
            placeholder="e.g. PicoCTF, HackTheBox, DefCon"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            disabled={disabled}
            className={FIELD_CLS}
          />
        </div>

        <div className="space-y-1">
          <label className="text-[10px] font-bold text-cyber-cyan uppercase tracking-wider flex items-center space-x-1">
            <Terminal className="w-3 h-3 text-cyber-cyan" />
            <span>3. Challenge Type</span>
            <span className="text-cyber-rose">*</span>
          </label>
          <input
            id="chat-challenge-type"
            type="text"
            placeholder="e.g. Web, Binary Exploitation, AI Reverse"
            value={type}
            onChange={(e) => setType(e.target.value)}
            disabled={disabled}
            className={FIELD_CLS}
          />
        </div>
      </div>

      <div className="flex justify-between items-center pt-1">
        <span className="text-[10px] text-slate-500">
          All 3 parameters required for operator telemetry initialization.
        </span>
        <button
          id="chat-turn1-submit"
          type="submit"
          disabled={!canSubmit}
          className={`flex items-center space-x-2 px-5 py-2 rounded-lg text-xs font-bold transition-all uppercase tracking-wider ${
            canSubmit
              ? 'bg-cyber-cyan text-obsidian-950 hover:bg-cyan-300 shadow-[0_0_15px_rgba(0,240,255,0.4)] hover:scale-[1.02]'
              : 'bg-obsidian-800 text-slate-600 border border-slate-800 cursor-not-allowed'
          }`}
        >
          <span>Next Turn</span>
          <Send className="w-3.5 h-3.5" />
        </button>
      </div>
    </form>
  );
};

// ---------------------------------------------------------------------------
// Turn 2 Form: Goal Description + Optional Target + Optional Files
// ---------------------------------------------------------------------------

const Turn2Form: React.FC<{
  onSubmit: (description: string, target: string, files: UploadedFile[]) => void;
  disabled: boolean;
}> = ({ onSubmit, disabled }) => {
  const [description, setDescription] = useState('');
  const [target, setTarget] = useState('');
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const canSubmit = description.trim() && !disabled && !uploading;

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    try {
      for (let i = 0; i < fileList.length; i++) {
        const up = await apiService.uploadArtifact(fileList[i]);
        setFiles((prev) => [...prev, { name: up.filename, path: up.path, size: up.size }]);
      }
      soundEngine.playSuccess();
    } catch (err) {
      console.warn('Artifact upload failed:', err);
    } finally {
      setUploading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canSubmit) {
      soundEngine.playClick();
      onSubmit(description.trim(), target.trim(), files);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="mt-4 pt-3 border-t border-cyan-500/20 space-y-3 font-mono">
      <div className="space-y-1">
        <label className="text-[10px] font-bold text-cyber-cyan uppercase tracking-wider block">
          Primary Goal / Description <span className="text-cyber-rose">*</span>
        </label>
        <textarea
          id="chat-description"
          rows={3}
          placeholder="Describe what you want the agent to accomplish (e.g., 'Discover SQL injection in login endpoint, dump SQLite credentials and extract the flag')."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={disabled}
          className={`${FIELD_CLS} resize-none`}
          autoFocus
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-[10px] font-bold text-slate-300 uppercase tracking-wider block">
            Target Address <span className="text-slate-500 font-normal lowercase">(optional URL, IP:Port, nc host port)</span>
          </label>
          <input
            id="chat-target-address"
            type="text"
            placeholder="e.g. http://10.10.11.45:8080 or nc chal.ctf.io 9000"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={disabled}
            className={FIELD_CLS}
          />
        </div>

        <div className="space-y-1">
          <label className="text-[10px] font-bold text-slate-300 uppercase tracking-wider block">
            Artifact Attachment <span className="text-slate-500 font-normal lowercase">(optional binary/pcap/src)</span>
          </label>
          <button
            id="chat-upload-btn"
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={disabled || uploading}
            className="w-full flex items-center justify-center space-x-2 border border-dashed border-cyan-500/40 hover:border-cyber-cyan bg-obsidian-950/60 rounded-lg py-2.5 text-xs text-slate-300 hover:text-cyber-cyan transition-colors"
          >
            {uploading ? (
              <Loader2 className="w-4 h-4 animate-spin text-cyber-cyan" />
            ) : (
              <Paperclip className="w-4 h-4 text-cyber-cyan" />
            )}
            <span>{uploading ? 'Staging Byte-Safe Upload…' : 'Attach Challenge File'}</span>
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
        </div>
      </div>

      {files.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {files.map((f, i) => (
            <div
              key={i}
              className="flex items-center space-x-2 bg-obsidian-900 border border-cyan-500/30 rounded-md px-2.5 py-1 text-[11px] text-slate-200"
            >
              <FileCode className="w-3.5 h-3.5 text-cyber-cyan shrink-0" />
              <span className="font-semibold">{f.name}</span>
              <span className="text-slate-500">({(f.size / 1024).toFixed(1)} KB)</span>
              <button
                type="button"
                onClick={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
                className="text-slate-500 hover:text-cyber-rose transition-colors ml-1"
                title="Remove attachment"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-between items-center pt-2">
        <span className="text-[10px] text-slate-500">
          Submitting commits challenge creation and launches agent workflow.
        </span>
        <button
          id="chat-turn2-submit"
          type="submit"
          disabled={!canSubmit}
          className={`flex items-center space-x-2 px-6 py-2.5 rounded-lg text-xs font-bold transition-all uppercase tracking-wider ${
            canSubmit
              ? 'bg-cyber-emerald text-obsidian-950 hover:bg-emerald-300 shadow-[0_0_18px_rgba(0,255,136,0.4)] hover:scale-[1.02]'
              : 'bg-obsidian-800 text-slate-600 border border-slate-800 cursor-not-allowed'
          }`}
        >
          <Sparkles className="w-4 h-4" />
          <span>Launch Challenge Workspace</span>
        </button>
      </div>
    </form>
  );
};

// ---------------------------------------------------------------------------
// Main Component: NewChallengeChat
// ---------------------------------------------------------------------------

export const NewChallengeChat: React.FC<NewChallengeChatProps> = ({
  onOpenWorkspace,
  onRefreshBackendData,
  setActiveTab,
  onClose
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentStep, setCurrentStep] = useState<number | string>(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  // Initialize chat session on mount
  const initSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRedirecting(false);
    try {
      const resp = await apiService.startChatSession();
      setSessionId(resp.session_id);
      setCurrentStep(resp.step);
      setMessages([
        {
          id: mkId(),
          role: 'bot',
          text: resp.bot_message,
          timestamp: new Date(),
          stepState: 1
        }
      ]);
    } catch (e: any) {
      setError(e?.message || 'Failed to establish challenge session with backend.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    initSession();
  }, [initSession]);

  const addUserMessage = (text: string, meta?: ChatMessage['meta']) => {
    setMessages((prev) => [
      ...prev,
      {
        id: mkId(),
        role: 'user',
        text,
        timestamp: new Date(),
        meta
      }
    ]);
  };

  const addBotMessage = (text: string, stepState?: 1 | 2 | 'committed') => {
    setMessages((prev) => [
      ...prev,
      {
        id: mkId(),
        role: 'bot',
        text,
        timestamp: new Date(),
        stepState
      }
    ]);
  };

  // Turn 1 handler
  const handleTurn1 = useCallback(
    async (name: string, platform: string, type: string) => {
      if (!sessionId) return;
      addUserMessage(
        `• **Challenge Name:** ${name}\n• **Platform / Event:** ${platform}\n• **Type:** ${type}`,
        { name, platform, type }
      );
      setLoading(true);
      setError(null);
      try {
        const resp = await apiService.sendChatMessage(sessionId, {
          challenge_name: name,
          platform_name: platform,
          challenge_type: type
        });
        setCurrentStep(resp.step);
        addBotMessage(resp.bot_message, 2);
        soundEngine.playSuccess();
      } catch (e: any) {
        setError(e?.message || 'Failed to send challenge metadata.');
      } finally {
        setLoading(false);
      }
    },
    [sessionId]
  );

  // Turn 2 handler
  const handleTurn2 = useCallback(
    async (description: string, target: string, files: UploadedFile[]) => {
      if (!sessionId) return;
      const fileNames = files.map((f) => f.name).join(', ');
      const userText = [
        `• **Goal / Description:** ${description}`,
        target ? `• **Target Address:** \`${target}\`` : null,
        files.length > 0 ? `• **Attached Files:** ${fileNames}` : null
      ]
        .filter(Boolean)
        .join('\n');

      addUserMessage(userText, {
        description,
        target,
        files: files.map((f) => ({ name: f.name, size: f.size }))
      });

      setLoading(true);
      setError(null);
      try {
        const resp = await apiService.sendChatMessage(sessionId, {
          description,
          target_address: target || undefined,
          attached_file_paths: files.map((f) => f.path)
        });

        setCurrentStep(resp.step);
        addBotMessage(resp.bot_message, 'committed');

        if (resp.step === 'committed' && resp.challenge) {
          soundEngine.playSuccess();
          setRedirecting(true);

          // Convert backend response to standard frontend Challenge object
          const raw = resp.challenge;
          const formattedChallenge: Challenge = {
            id: raw.id,
            name: raw.name,
            category: raw.category,
            difficulty: raw.difficulty || 'MEDIUM',
            target: raw.target_address || (raw.targets && raw.targets[0]?.current_address) || target || '127.0.0.1',
            status: raw.status || 'RUNNING',
            progress: raw.progress || 0,
            lastActivity: 'Just now',
            flagStatus: raw.flag_status || 'UNFOUND',
            flag: raw.flag,
            description: raw.description || description,
            workingDirectory: raw.working_directory,
            platformName: raw.platform_name,
            createdAt: raw.created_at,
            created_at: raw.created_at,
            startedAt: raw.started_at,
            started_at: raw.started_at,
            missionPlan: raw.mission_plan,
            mission_plan: raw.mission_plan
          };

          // Trigger backend data refresh to keep lists in sync
          if (onRefreshBackendData) {
            await onRefreshBackendData();
          }

          // Route directly into the investigation workspace / pipeline view
          setTimeout(() => {
            onOpenWorkspace(formattedChallenge);
          }, 800);
        }
      } catch (e: any) {
        setError(e?.message || 'Failed to complete challenge creation.');
      } finally {
        setLoading(false);
      }
    },
    [sessionId, onRefreshBackendData, onOpenWorkspace]
  );

  return (
    <div className="flex flex-col h-[calc(100vh-6.5rem)] font-mono text-slate-100 select-text max-w-5xl mx-auto w-full">
      {/* Header bar matching Forge standards */}
      <div className="flex items-center justify-between p-4 glass-panel rounded-xl border border-cyan-500/20 shrink-0 mb-4 bg-obsidian-950/80 shadow-[0_0_20px_rgba(0,0,0,0.5)]">
        <div className="flex items-center space-x-3.5">
          <div className="w-10 h-10 rounded-lg bg-obsidian-900 border border-cyber-cyan/50 flex items-center justify-center shadow-[0_0_15px_rgba(0,240,255,0.25)]">
            <MessageSquare className="w-5 h-5 text-cyber-cyan" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-sm font-display font-bold tracking-wider text-slate-100 uppercase neon-text-cyan">
                Conversational Challenge Ingestion
              </h1>
              <span className="px-2 py-0.5 rounded text-[9px] font-bold bg-cyan-950/80 border border-cyber-cyan/40 text-cyber-cyan uppercase">
                Interactive Assistant
              </span>
            </div>
            <p className="text-[11px] text-slate-400">
              Two-turn agent intake flow — collect target parameters, objectives, and auto-dispatch
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={() => {
              soundEngine.playClick();
              initSession();
            }}
            title="Reset Chat Session"
            className="p-2 rounded-lg border border-slate-800 hover:border-cyber-cyan/40 bg-obsidian-900 text-slate-400 hover:text-cyber-cyan transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            id="chat-back-btn"
            onClick={() => {
              soundEngine.playClick();
              if (onClose) {
                onClose();
              } else if (setActiveTab) {
                setActiveTab('challenges');
              }
            }}
            className="flex items-center space-x-2 text-xs text-slate-300 hover:text-cyber-cyan px-3.5 py-2 border border-slate-800 hover:border-cyber-cyan/40 rounded-lg bg-obsidian-900 transition-colors uppercase tracking-wider font-bold"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Challenges List</span>
          </button>
        </div>
      </div>

      {/* Main Conversation Stream */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-1 pb-4 cyber-scrollbar">
        {/* Connection Loader */}
        {loading && messages.length === 0 && (
          <div className="flex flex-col items-center justify-center p-12 glass-panel rounded-xl border border-cyan-500/20 text-slate-400 space-y-3">
            <Loader2 className="w-8 h-8 animate-spin text-cyber-cyan" />
            <span className="text-xs tracking-wider uppercase font-bold text-cyber-cyan">
              Initializing AI Challenge Ingestion Session…
            </span>
          </div>
        )}

        {/* Global Error Banner */}
        {error && (
          <div className="flex items-start space-x-3 bg-rose-950/40 border border-cyber-rose/60 rounded-xl p-3.5 text-xs text-rose-300 shadow-[0_0_15px_rgba(255,0,85,0.2)]">
            <AlertTriangle className="w-4 h-4 shrink-0 text-cyber-rose mt-0.5" />
            <div className="flex-1">
              <span className="font-bold uppercase tracking-wider block text-cyber-rose">Ingestion Error</span>
              <span>{error}</span>
            </div>
            <button
              onClick={() => setError(null)}
              className="text-rose-400 hover:text-white"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Messages List */}
        {messages.map((msg, idx) => {
          const isBot = msg.role === 'bot';
          const isLastMsg = idx === messages.length - 1;

          return (
            <div
              key={msg.id}
              className={`flex items-start ${isBot ? 'justify-start' : 'justify-end'} space-x-3`}
            >
              {isBot && (
                <div className="w-8 h-8 rounded-lg bg-obsidian-900 border border-cyber-cyan/40 flex items-center justify-center shrink-0 shadow-[0_0_12px_rgba(0,240,255,0.2)] mt-0.5">
                  <Bot className="w-4 h-4 text-cyber-cyan" />
                </div>
              )}

              <div
                className={`max-w-[88%] md:max-w-[78%] rounded-xl p-4 transition-all ${
                  isBot
                    ? 'bg-obsidian-900/90 border border-cyan-500/30 text-slate-200 shadow-[0_0_20px_rgba(0,0,0,0.4)]'
                    : 'bg-gradient-to-br from-cyan-950/60 to-obsidian-900 border border-cyber-cyan/40 text-slate-100 shadow-[0_0_15px_rgba(0,240,255,0.15)]'
                }`}
              >
                <div className="flex items-center justify-between mb-1.5 pb-1 border-b border-white/5">
                  <div className="flex items-center space-x-2">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-cyber-cyan">
                      {isBot ? 'FORGE Ingestion Assistant' : 'Operator'}
                    </span>
                    {msg.stepState === 1 && (
                      <span className="text-[9px] px-1.5 py-0.2 rounded bg-cyan-950 border border-cyber-cyan/30 text-cyber-cyan">
                        Step 1 of 2
                      </span>
                    )}
                    {msg.stepState === 2 && (
                      <span className="text-[9px] px-1.5 py-0.2 rounded bg-emerald-950 border border-cyber-emerald/30 text-cyber-emerald">
                        Step 2 of 2
                      </span>
                    )}
                    {msg.stepState === 'committed' && (
                      <span className="text-[9px] px-1.5 py-0.2 rounded bg-emerald-950 border border-cyber-emerald/50 text-cyber-emerald font-bold">
                        Committed
                      </span>
                    )}
                  </div>
                  <span className="text-[9px] text-slate-500 font-mono">
                    {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                </div>

                <div className="text-xs leading-relaxed whitespace-pre-wrap font-sans">
                  {renderMarkdown(msg.text)}
                </div>

                {/* Turn 1 Input Form inline on the active prompt */}
                {isBot && isLastMsg && currentStep === 1 && !loading && (
                  <Turn1Form onSubmit={handleTurn1} disabled={loading} />
                )}

                {/* Turn 2 Input Form inline on the active prompt */}
                {isBot && isLastMsg && currentStep === 2 && !loading && (
                  <Turn2Form onSubmit={handleTurn2} disabled={loading} />
                )}

                {/* Redirecting Banner */}
                {isBot && isLastMsg && (currentStep === 'committed' || redirecting) && (
                  <div className="mt-3 pt-3 border-t border-emerald-500/30 flex items-center space-x-2.5 text-xs text-cyber-emerald bg-emerald-950/30 p-2.5 rounded-lg border border-emerald-500/20">
                    <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                    <span className="font-bold uppercase tracking-wider">
                      Challenge created! Routing to Investigation Workspace…
                    </span>
                  </div>
                )}
              </div>

              {!isBot && (
                <div className="w-8 h-8 rounded-lg bg-cyan-950 border border-cyber-cyan/60 flex items-center justify-center shrink-0 shadow-[0_0_12px_rgba(0,240,255,0.3)] mt-0.5">
                  <User className="w-4 h-4 text-cyber-cyan" />
                </div>
              )}
            </div>
          );
        })}

        {/* Processing Indicator */}
        {loading && messages.length > 0 && (
          <div className="flex items-center space-x-3 text-xs text-slate-400 pl-11 py-2">
            <div className="w-6 h-6 rounded-md bg-obsidian-900 border border-cyber-cyan/40 flex items-center justify-center">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-cyber-cyan" />
            </div>
            <span className="text-cyber-cyan font-bold uppercase tracking-wider text-[11px] animate-pulse">
              FORGE is evaluating inputs & compiling challenge pipeline…
            </span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
};
