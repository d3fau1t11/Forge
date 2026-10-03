import React, { useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import rehypeSanitize from 'rehype-sanitize';
import 'highlight.js/styles/github-dark.min.css';
import { Copy, Check } from 'lucide-react';
import { soundEngine } from '../../utils/soundEngine';

// Sanitize schema: allow common markdown elements + code blocks with language classes
const sanitizeSchema = {
  tagNames: [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'br', 'hr',
    'strong', 'em', 'u', 's', 'code', 'pre',
    'a', 'blockquote',
    'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'img',
    'div', 'span'
  ],
  attributes: {
    a: ['href', 'title', 'target', 'rel'],
    img: ['src', 'alt', 'title'],
    th: ['align'],
    td: ['align'],
    '*': ['className']
  },
  clobberPrefix: 'user-content-',
  strip: ['script', 'style']
};

// Custom components for FORGE styling
interface CodeBlockProps extends React.HTMLAttributes<HTMLPreElement> {
  children?: React.ReactNode;
}

function CodeBlock({ children, className }: CodeBlockProps) {
  // Extract language from className (e.g., "language-typescript")
  const language = className?.replace('language-', '') || 'bash';
  const [copied, setCopied] = React.useState(false);

  const handleCopy = useCallback(async () => {
    try {
      const codeText = typeof children === 'string' ? children : '';
      await navigator.clipboard.writeText(codeText);
      setCopied(true);
      soundEngine.playClick();
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback handled by browser
    }
  }, [children]);

  return (
    <div className="relative group my-3">
      <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 z-10">
        <button
          onClick={handleCopy}
          className="px-2 py-1 rounded bg-obsidian-900 border border-slate-700 text-[10px] font-mono text-slate-300 hover:text-cyber-cyan hover:border-cyber-cyan/50 transition-colors flex items-center space-x-1"
          aria-label="Copy code block"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-cyber-emerald" />
              <span>Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <div className="bg-obsidian-950 border border-slate-800 rounded-lg overflow-hidden">
        {language && (
          <div className="bg-obsidian-900 px-3 py-1.5 border-b border-slate-800 flex items-center justify-between">
            <span className="text-[10px] font-mono text-cyber-cyan uppercase tracking-wider font-bold">{language}</span>
          </div>
        )}
        <pre className="p-4 overflow-x-auto text-[12px] leading-relaxed">
          <code className={`language-${language} hljs`}>{children}</code>
        </pre>
      </div>
    </div>
  );
}

function InlineCode({ children, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <code
      {...props}
      className="bg-obsidian-900 border border-cyber-amber/30 text-amber-300 px-1.5 py-0.5 rounded text-[11px] font-mono"
    >
      {children}
    </code>
  );
}

function Blockquote({ children, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <blockquote
      {...props}
      className="border-l-3 border-cyber-cyan/50 pl-4 my-3 text-slate-300 italic text-xs"
    >
      {children}
    </blockquote>
  );
}

function Table({ children, ...props }: React.HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto my-3">
      <table {...props} className="min-w-full text-xs border border-slate-800">
        {children}
      </table>
    </div>
  );
}

function Th({ children, ...props }: React.HTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      {...props}
      className="bg-obsidian-900 border border-slate-800 px-3 py-2 text-left font-bold text-cyber-cyan"
    >
      {children}
    </th>
  );
}

function Td({ children, ...props }: React.HTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      {...props}
      className="border border-slate-800 px-3 py-2 text-slate-200"
    >
      {children}
    </td>
  );
}

function Hr({ ...props }: React.HTMLAttributes<HTMLHRElement>) {
  return <hr {...props} className="border-slate-800 my-4" />;
}

const components = {
  pre: CodeBlock,
  code: InlineCode,
  blockquote: Blockquote,
  table: Table,
  th: Th,
  td: Td,
  hr: Hr,
  a: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-cyber-cyan hover:text-cyan-300 underline underline-offset-2"
      {...props}
    >
      {children}
    </a>
  )
};

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
  content,
  className = ''
}) => {
  if (!content) return null;

  return (
    <div className={`prose prose-invert max-w-none text-xs font-mono ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          rehypeHighlight,
          [rehypeSanitize, sanitizeSchema]
        ]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};

// Standalone component for message-level copy button
export const MessageCopyButton: React.FC<{ 
  content: string; 
  onCopied?: () => void;
  className?: string;
}> = ({ content, onCopied, className = '' }) => {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      soundEngine.playClick();
      onCopied?.();
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback handled by browser
    }
  }, [content, onCopied]);

  return (
    <button
      onClick={handleCopy}
      className={`px-2 py-1 rounded bg-obsidian-900 border border-slate-700 text-[10px] font-mono text-slate-400 hover:text-cyber-cyan hover:border-cyber-cyan/50 transition-colors flex items-center space-x-1 ${className}`}
      aria-label="Copy message"
    >
      {copied ? (
        <>
          <Check className="w-3 h-3 text-cyber-emerald" />
          <span>Copied</span>
        </>
      ) : (
        <>
          <Copy className="w-3 h-3" />
          <span>Copy</span>
        </>
      )}
    </button>
  );
};