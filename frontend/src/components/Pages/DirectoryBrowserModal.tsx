import React, { useState, useEffect } from 'react';
import { Folder, FolderPlus, ArrowUp, HardDrive, Check, X, Monitor, RefreshCw } from 'lucide-react';
import { apiService } from '../../services/api';
import { soundEngine } from '../../utils/soundEngine';

interface DirectoryBrowserModalProps {
  initialPath?: string;
  onSelect: (selectedPath: string) => void;
  onClose: () => void;
}

export const DirectoryBrowserModal: React.FC<DirectoryBrowserModalProps> = ({
  initialPath,
  onSelect,
  onClose
}) => {
  const [currentPath, setCurrentPath] = useState<string>(initialPath || '');
  const [parentPath, setParentPath] = useState<string>('');
  const [drives, setDrives] = useState<string[]>([]);
  const [directories, setDirectories] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(true);

  // New directory creation state
  const [showNewFolderInput, setShowNewFolderInput] = useState<boolean>(false);
  const [newFolderName, setNewFolderName] = useState<string>('');

  const loadDirectory = async (targetPath?: string) => {
    setLoading(true);
    try {
      const res = await apiService.browseDirectory(targetPath);
      if (res) {
        setCurrentPath(res.current_path);
        setParentPath(res.parent_path);
        setDrives(res.drives || []);
        setDirectories(res.directories || []);
      }
    } catch (e) {
      console.warn('Failed to load directory:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDirectory(initialPath);
  }, []);

  const handleOpenNativeDialog = async () => {
    soundEngine.playClick();
    
    // First try HTML5 File System Access API if available
    if ('showDirectoryPicker' in window) {
      try {
        const handle = await (window as any).showDirectoryPicker();
        if (handle && handle.name) {
          // If browser gives handle, path is resolved or use currentPath + handle.name
          const selectedName = handle.name;
          const fullPath = currentPath ? `${currentPath}/${selectedName}` : selectedName;
          onSelect(fullPath);
          onClose();
          return;
        }
      } catch (err) {
        // Fallback to backend OS dialog if user cancels or browser restricts
      }
    }

    // Fallback to backend Tkinter/OS native folder picker
    const res = await apiService.selectFolderDialog();
    if (res && res.status === 'SUCCESS' && res.selected_path) {
      soundEngine.playSuccess();
      onSelect(res.selected_path);
      onClose();
    }
  };

  const handleCreateFolder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newFolderName.trim()) return;
    soundEngine.playSuccess();
    try {
      const res = await apiService.createDirectory(currentPath, newFolderName.trim());
      if (res && res.created_path) {
        setNewFolderName('');
        setShowNewFolderInput(false);
        await loadDirectory(res.created_path);
      }
    } catch (err) {
      console.error('Directory creation failed:', err);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 backdrop-blur-md p-4 animate-fadeIn">
      <div className="w-full max-w-xl max-h-[90vh] flex flex-col bg-obsidian-950 border-2 border-cyber-cyan/70 rounded-xl shadow-[0_0_60px_rgba(0,240,255,0.3)] font-mono text-slate-100 cyber-corner overflow-hidden my-auto">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 p-5 shrink-0 bg-obsidian-950">
          <div className="flex items-center space-x-2">
            <Folder className="w-5 h-5 text-cyber-cyan" />
            <h3 className="font-display font-bold text-sm tracking-wider uppercase text-slate-100 neon-text-cyan">
              NAVIGATE & SELECT WORKING DIRECTORY
            </h3>
          </div>
          <button
            onClick={() => { soundEngine.playClick(); onClose(); }}
            className="p-1 rounded bg-obsidian-900 hover:bg-rose-950 text-slate-400 hover:text-cyber-rose transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable Content Body */}
        <div className="p-5 space-y-4 flex-1 overflow-y-auto min-h-0 custom-scrollbar">
          {/* Current Path Bar & Quick Native Pop-up Button */}
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-slate-400 font-bold uppercase">Current Folder Path:</span>
              <button
                onClick={handleOpenNativeDialog}
                className="px-3 py-1 bg-cyber-cyan/20 hover:bg-cyber-cyan/35 border border-cyber-cyan text-cyber-cyan rounded text-[11px] font-bold flex items-center space-x-1.5 transition-all shadow-[0_0_10px_rgba(0,240,255,0.2)]"
              >
                <Monitor className="w-3.5 h-3.5" />
                <span>[ OPEN OS NATIVE POPUP ]</span>
              </button>
            </div>
            <div className="bg-obsidian-900 p-2.5 rounded-lg border border-slate-800 text-xs font-bold text-cyber-cyan truncate flex items-center space-x-2">
              <Folder className="w-4 h-4 text-cyber-cyan shrink-0" />
              <span className="truncate">{currentPath || 'Select path...'}</span>
            </div>
          </div>

          {/* Quick Toolbar: Parent Dir, Refresh, Drives */}
          <div className="flex items-center justify-between gap-2 text-xs">
            <div className="flex items-center space-x-2 overflow-x-auto">
              <button
                disabled={!parentPath || parentPath === currentPath}
                onClick={() => { soundEngine.playClick(); loadDirectory(parentPath); }}
                className="px-2.5 py-1.5 rounded bg-obsidian-900 border border-slate-800 hover:border-cyber-cyan text-slate-300 disabled:opacity-40 flex items-center space-x-1 font-bold"
                title="Go Up to Parent Folder"
              >
                <ArrowUp className="w-3.5 h-3.5 text-cyber-cyan" />
                <span>UP</span>
              </button>

              <button
                onClick={() => { soundEngine.playClick(); loadDirectory(currentPath); }}
                className="p-1.5 rounded bg-obsidian-900 border border-slate-800 hover:border-cyber-cyan text-slate-300"
                title="Refresh Folder Contents"
              >
                <RefreshCw className={`w-3.5 h-3.5 text-slate-400 ${loading ? 'animate-spin' : ''}`} />
              </button>

              {/* System Drives (Windows / Linux) */}
              {drives.map((drv) => (
                <button
                  key={drv}
                  onClick={() => { soundEngine.playClick(); loadDirectory(drv); }}
                  className={`px-2.5 py-1 rounded text-xs font-bold flex items-center space-x-1 border ${
                    currentPath.startsWith(drv)
                      ? 'bg-cyber-cyan/20 border-cyber-cyan text-cyber-cyan'
                      : 'bg-obsidian-900 border-slate-800 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <HardDrive className="w-3 h-3" />
                  <span>{drv}</span>
                </button>
              ))}
            </div>

            <button
              onClick={() => { soundEngine.playClick(); setShowNewFolderInput(!showNewFolderInput); }}
              className="px-3 py-1.5 rounded bg-emerald-950/80 hover:bg-emerald-900 border border-emerald-700 text-cyber-emerald text-xs font-bold flex items-center space-x-1.5 shrink-0"
            >
              <FolderPlus className="w-3.5 h-3.5" />
              <span>[ + NEW FOLDER ]</span>
            </button>
          </div>

          {/* New Folder Form Input */}
          {showNewFolderInput && (
            <form onSubmit={handleCreateFolder} className="p-3 bg-emerald-950/40 border border-emerald-800 rounded-lg flex items-center gap-2 text-xs animate-fadeIn">
              <input
                type="text"
                autoFocus
                placeholder="Enter new folder name (e.g. web_challenge_1)"
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.target.value)}
                className="flex-1 bg-obsidian-900 border border-slate-700 rounded px-3 py-1.5 text-slate-100 focus:outline-none focus:border-cyber-emerald font-mono"
              />
              <button
                type="submit"
                className="px-3 py-1.5 bg-cyber-emerald text-obsidian-950 font-bold rounded hover:bg-emerald-400 uppercase tracking-wider"
              >
                CREATE
              </button>
            </form>
          )}

          {/* Directory Listing List View */}
          <div className="h-48 md:h-56 overflow-y-auto bg-obsidian-900 border border-slate-800 rounded-lg p-2 space-y-1">
            {loading ? (
              <div className="h-full flex items-center justify-center text-xs text-slate-400 space-x-2">
                <RefreshCw className="w-4 h-4 animate-spin text-cyber-cyan" />
                <span>Reading folder structure...</span>
              </div>
            ) : directories.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-xs text-slate-500 space-y-1">
                <Folder className="w-6 h-6 text-slate-700" />
                <span>Folder is empty or contains no subdirectories.</span>
                <span className="text-[10px] text-cyber-cyan">Click [ SELECT THIS FOLDER ] below to use current path.</span>
              </div>
            ) : (
              directories.map((dirName) => (
                <div
                  key={dirName}
                  onDoubleClick={() => {
                    soundEngine.playClick();
                    const nextPath = currentPath.endsWith('/') || currentPath.endsWith('\\')
                      ? `${currentPath}${dirName}`
                      : `${currentPath}/${dirName}`;
                    loadDirectory(nextPath);
                  }}
                  onClick={() => {
                    soundEngine.playClick();
                  }}
                  className="flex items-center justify-between p-2 rounded hover:bg-obsidian-800 border border-transparent hover:border-slate-800 cursor-pointer text-xs transition-colors group"
                >
                  <div className="flex items-center space-x-2.5 min-w-0">
                    <Folder className="w-4 h-4 text-cyber-cyan group-hover:scale-110 transition-transform shrink-0" />
                    <span className="text-slate-200 font-semibold truncate group-hover:text-cyber-cyan">{dirName}</span>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      soundEngine.playClick();
                      const nextPath = currentPath.endsWith('/') || currentPath.endsWith('\\')
                        ? `${currentPath}${dirName}`
                        : `${currentPath}/${dirName}`;
                      loadDirectory(nextPath);
                    }}
                    className="px-2 py-0.5 rounded bg-obsidian-950 border border-slate-800 text-[10px] text-slate-400 hover:text-cyber-cyan hover:border-cyber-cyan"
                  >
                    OPEN
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Sticky Footer Controls */}
        <div className="p-4 border-t border-slate-800 flex items-center justify-between text-xs shrink-0 bg-obsidian-950">
          <span className="text-[10px] text-slate-500 hidden sm:inline">Double click subfolders to navigate inside</span>
          <div className="flex items-center space-x-3 ml-auto">
            <button
              type="button"
              onClick={() => { soundEngine.playClick(); onClose(); }}
              className="px-4 py-2 rounded bg-obsidian-900 text-slate-400 hover:text-slate-200 font-bold transition-colors"
            >
              CANCEL
            </button>
            <button
              type="button"
              onClick={() => {
                soundEngine.playSuccess();
                onSelect(currentPath);
                onClose();
              }}
              className="px-5 py-2 rounded bg-cyber-cyan hover:bg-cyan-300 text-obsidian-950 font-display font-bold uppercase tracking-wider flex items-center space-x-1.5 shadow-[0_0_15px_rgba(0,240,255,0.4)] transition-all hover:scale-105"
            >
              <Check className="w-4 h-4" />
              <span>SELECT THIS FOLDER</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
