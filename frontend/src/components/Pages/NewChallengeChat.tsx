/**
 * NewChallengeChat — Pure conversational challenge creation for FORGE.
 *
 * The chat collects exactly four fields through natural language:
 *   1. Challenge Name
 *   2. Platform / Event Name
 *   3. Challenge Type / Category
 *   4. Difficulty (EASY/MEDIUM/HARD/INSANE)
 *
 * The backend drives the conversation: it asks only for missing fields,
 * normalizes category/difficulty, and creates the challenge when all
 * four are present. No forms are ever rendered.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  MessageSquare,
  X,
  ArrowLeft,
  Loader2,
  AlertTriangle,
  Send,
  Bot,
  User,
  RefreshCw,
  FileCode,
  Paperclip
} from 'lucide-react';
import { apiService } from '../../services/api';
import { Challenge, NavTab } from '../../types';
import { soundEngine } from '../../utils/soundEngine';
import { MarkdownRenderer, MessageCopyButton } from '../UI/MarkdownRenderer';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatMessage {
  id: string;
  role: 'bot' | 'user';
  text: string;
  timestamp: Date;
  stepState?: 1 | 2 | 'committed' | 'collecting';
  meta?: {
    name?: string;
    platform?: string;
    type?: string;
    difficulty?: string;
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

const INPUT_CLS =
  'w-full bg-obsidian-950/90 border border-cyan-500/30 rounded-lg px-3.5 py-2.5 text-xs text-slate-100 ' +
  'placeholder:text-slate-500 focus:outline-none focus:border-cyber-cyan focus:ring-1 focus:ring-cyber-cyan/40 transition-all font-mono';

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
  const [inputValue, setInputValue] = useState('');
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
    setInputValue('');
    setFiles([]);
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
          stepState: 'collecting'
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

  const addBotMessage = (text: string, stepState?: 1 | 2 | 'committed' | 'collecting') => {
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

  // Single unified handler: send whatever the operator typed, let backend
  // decide what's missing and respond accordingly.
  const handleSend = useCallback(async () => {
    if (!sessionId || (!inputValue.trim() && files.length === 0) || loading) return;

    const text = inputValue.trim();
    const fileNames = files.map((f) => f.name).join(', ');
    const userText = [
      text,
      files.length > 0 ? `• **Attached Files:** ${fileNames}` : null
    ].filter(Boolean).join('\n');

    addUserMessage(userText, {
      description: text,
      files: files.map((f) => ({ name: f.name, size: f.size }))
    });

    // Clear input immediately for responsive feel
    setInputValue('');
    const sentFiles = [...files];
    setFiles([]);
    setLoading(true);
    setError(null);

    try {
      const payload: {
        challenge_name?: string;
        platform_name?: string;
        challenge_type?: string;
        difficulty?: string;
        target_address?: string;
        description?: string;
        attached_file_paths?: string[];
      } = {
        attached_file_paths: sentFiles.map((f) => f.path)
      };

      // We don't know which fields the backend still needs — send everything
      // the operator has provided so far. The backend will ignore extras and
      // ask only for what's still missing.
      // Heuristics: if we have a session, we can parse the operator's message
      // for structured fields, but the backend already does incremental
      // collection. Just send the raw text as description and let the backend
      // handle field extraction on step 2, while on step 1 we send any
      // structured fields we can infer.
      //
      // Simpler approach: on step 1, the backend expects structured fields.
      // On step 2, it expects description + optional target + files.
      // Since we're doing pure chat, we send the raw message as description
      // on step 2, and on step 1 we try to extract structured fields.
      //
      // Actually, the backend's turn 1 handler accepts challenge_name,
      // platform_name, challenge_type, difficulty. The turn 2 handler
      // accepts description, target_address, attached_file_paths.
      //
      // We'll send the user's raw text as `description` on both steps, plus
      // any structured fields we can parse. The backend's incremental logic
      // will pick up what it needs.

      if (currentStep === 1) {
        // Try to extract structured fields from the user's message
        // This is best-effort; the backend will ask for whatever's missing
        const lower = text.toLowerCase();
        
        // Extract name if it looks like a name (first message, short)
        if (!messages.some(m => m.meta?.name)) {
          // Heuristic: if message is short and doesn't contain common field keywords
          if (text.length < 80 && !lower.includes('platform') && !lower.includes('type') && !lower.includes('category') && !lower.includes('difficulty') && !lower.includes('easy') && !lower.includes('medium') && !lower.includes('hard') && !lower.includes('insane')) {
            payload.challenge_name = text;
          }
        }
        
        // Try to extract platform
        const platformMatch = text.match(/(?:platform|event)[:\s]+([^\n,]+)/i);
        if (platformMatch) payload.platform_name = platformMatch[1].trim();
        
        // Try to extract type/category
        const typeMatch = text.match(/(?:type|category)[:\s]+([^\n,]+)/i);
        if (typeMatch) payload.challenge_type = typeMatch[1].trim();
        
        // Try to extract difficulty
        const diffMatch = text.match(/\b(easy|medium|hard|insane|beginner|trivial|advanced|expert|extreme|elite)\b/i);
        if (diffMatch) payload.difficulty = diffMatch[1].toUpperCase();
      } else if (currentStep === 2) {
        // Turn 2: description is the main field, plus optional target
        payload.description = text;
        
        // Try to extract target address from the message
        const targetMatch = text.match(/(?:target|address|ip|url|nc\s+)[:\s]*(\S+(?:\s+\d+)?)/i);
        if (targetMatch) {
          payload.target_address = targetMatch[1].trim();
        }
      }

      const resp = await apiService.sendChatMessage(sessionId, payload);
      setCurrentStep(resp.step);

      if (resp.step === 'committed' && resp.challenge) {
        addBotMessage(resp.bot_message, 'committed');
        soundEngine.playSuccess();
        setRedirecting(true);

        // Convert backend response to standard frontend Challenge object
        const raw = resp.challenge;
        const formattedChallenge: Challenge = {
          id: raw.id,
          name: raw.name,
          category: raw.category,
          difficulty: raw.difficulty || 'MEDIUM',
          target: raw.target_address || (raw.targets && raw.targets[0]?.current_address) || '127.0.0.1',
          status: raw.status || 'RUNNING',
          progress: raw.progress || 0,
          lastActivity: 'Just now',
          flagStatus: raw.flag_status || 'UNFOUND',
          flag: raw.flag,
          description: raw.description || text,
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
      } else {
        addBotMessage(resp.bot_message, resp.step === 1 ? 'collecting' : 2);
        soundEngine.playSuccess();
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to communicate with backend.');
    } finally {
      setLoading(false);
    }
  }, [sessionId, currentStep, inputValue, files, messages, onRefreshBackendData, onOpenWorkspace]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files);
    e.target.value = '';
  };

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
              Natural-language challenge creation — the assistant asks only for what it still needs
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
              className={`flex items-end space-x-3 gap-3 ${isBot ? 'flex-row' : 'flex-row-reverse'}`}
            >
              {/* Avatar */}
              <div
                className={`w-8 h-8 rounded-full shrink-0 flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                  isBot
                    ? 'bg-cyan-950 border border-cyber-cyan/50 text-cyber-cyan'
                    : 'bg-cyan-950 border border-cyber-cyan/50 text-cyber-cyan'
                }`}
              >
                {isBot ? <Bot className="w-4 h-4" /> : <User className="w-4 h-4" />}
              </div>

              {/* Message Bubble */}
              <div className={`flex-1 min-w-0 max-w-[85%] ${isBot ? 'order-first' : 'order-last'}`}>
                <div
                  className={`rounded-2xl px-4 py-3 shadow-sm border transition-all ${
                    isBot
                      ? 'bg-obsidian-950/80 border-cyan-500/20 text-slate-200 rounded-bl-md'
                      : 'bg-cyan-950/50 border-cyan-500/30 text-slate-100 rounded-br-md'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className={`font-medium text-[11px] uppercase tracking-wider ${isBot ? 'text-cyan-300' : 'text-cyan-300'}`}>
                      {isBot ? 'FORGE Ingestion Assistant' : 'Operator'}
                    </span>
                    <div className="flex items-center gap-1.5 ml-auto">
                      <span className="text-slate-500 text-[10px] whitespace-nowrap">
                        {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                      {isBot && <MessageCopyButton content={msg.text} className="opacity-0 group-hover:opacity-100 transition-opacity" />}
                    </div>
                  </div>
                  <div className="text-xs leading-relaxed font-sans select-text prose prose-invert prose-sm max-w-none m-0">
                    {isBot ? <MarkdownRenderer content={msg.text} className="font-sans" /> : <div className="whitespace-pre-wrap font-mono">{msg.text}</div>}
                  </div>

                  {/* Attached files display */}
                  {msg.meta?.files && msg.meta.files.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {msg.meta.files.map((f, i) => (
                        <div
                          key={i}
                          className="flex items-center space-x-2 bg-obsidian-900 border border-cyan-500/30 rounded-md px-2.5 py-1 text-[11px] text-slate-200"
                        >
                          <FileCode className="w-3.5 h-3.5 text-cyber-cyan shrink-0" />
                          <span className="font-semibold">{f.name}</span>
                          <span className="text-slate-500">({(f.size / 1024).toFixed(1)} KB)</span>
                        </div>
                      ))}
                    </div>
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
              </div>
            </div>
          );
        })}

        {/* Processing Indicator */}
        {loading && messages.length > 0 && (
          <div className="flex items-end space-x-3">
            <div className="w-8 h-8 rounded-full bg-cyan-950 border border-cyber-cyan/50 flex items-center justify-center text-cyan-300 flex-shrink-0">
              <Bot className="w-4 h-4 animate-bounce" />
            </div>
            <div className="flex-1 min-w-0 max-w-[85%]">
              <div className="rounded-2xl px-4 py-3 shadow-sm border bg-obsidian-950/80 border-cyan-500/20 text-slate-200 rounded-bl-md">
                <div className="flex items-center space-x-2 text-cyan-300">
                  <Loader2 className="w-4 h-4 animate-spin text-cyber-cyan" />
                  <span className="font-medium text-[11px] uppercase tracking-wider animate-pulse">FORGE is evaluating inputs & compiling challenge pipeline…</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Input Area — always at bottom */}
        <div className="shrink-0 pt-3 border-t border-cyan-500/20">
          {/* File attachments preview */}
          {(files.length > 0 || uploading) && (
            <div className="flex flex-wrap gap-2 mb-3">
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
              {uploading && (
                <div className="flex items-center space-x-2 text-xs text-cyber-cyan">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Staging upload…</span>
                </div>
              )}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="glass-panel border border-slate-800/50 p-3 rounded-2xl flex items-end space-x-3 bg-obsidian-950/80 shadow-xl"
          >
            <div className="flex-1 relative">
              <textarea
                ref={inputRef}
                id="chat-input"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={loading}
                placeholder={loading ? 'Processing…' : 'Type your response… (Shift+Enter for new line)'}
                rows={1}
                className={`${INPUT_CLS} resize-none ${loading ? 'opacity-50' : ''} min-h-[44px] max-h-32`}
                autoFocus
              />
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={loading || uploading}
                className="absolute bottom-2 right-2 p-1.5 rounded-lg bg-obsidian-900 border border-slate-800 hover:border-cyber-cyan/40 text-slate-400 hover:text-cyber-cyan transition-colors"
                title="Attach challenge file"
              >
                <Paperclip className="w-4 h-4" />
              </button>
              <input
                type="file"
                multiple
                className="hidden"
                onChange={handleFileSelect}
              />
            </div>
            <button
              id="chat-send-btn"
              type="submit"
              disabled={loading || (!inputValue.trim() && files.length === 0)}
              className={`flex-shrink-0 p-3 rounded-xl transition-all ${
                loading || (!inputValue.trim() && files.length === 0)
                  ? 'bg-obsidian-800 text-slate-600 border border-slate-800 cursor-not-allowed'
                  : 'bg-cyber-emerald text-obsidian-950 hover:bg-emerald-300 shadow-[0_0_15px_rgba(0,255,136,0.4)] hover:scale-[1.02]'
              }`}
              title="Send"
            >
              <Send className="w-5 h-5" />
            </button>
          </form>
          <p className="text-[10px] text-slate-500 mt-1 text-center">
            The assistant will ask only for missing fields. No forms — just conversation.
          </p>
        </div>

        <div ref={bottomRef} />
      </div>
    </div>
  );
};