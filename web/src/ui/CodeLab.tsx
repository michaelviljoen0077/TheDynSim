import { useEffect, useMemo, useState } from 'react';
import { useStore } from '../state/store';
import { fetchJson, type LabPlugin } from '../net/api';
import './codelab.css';

type Tab = 'source' | 'diff' | 'tree';
type DiffKind = 'same' | 'add' | 'del';
interface DiffLine {
  kind: DiffKind;
  text: string;
}

function fateClass(fate: string): string {
  if (fate === 'live' || fate === 'promoted') return 'good';
  if (fate === 'scored') return 'info';
  if (fate === 'quarantined' || fate.startsWith('rejected') || fate === 'promotion_failed') {
    return 'bad';
  }
  return 'neutral';
}

// LCS-based line diff of parent (old) vs child (new). Dependency-free.
function lineDiff(oldSrc: string, newSrc: string): DiffLine[] {
  const A = oldSrc.split('\n');
  const B = newSrc.split('\n');
  const m = A.length;
  const n = B.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (A[i] === B[j]) {
      out.push({ kind: 'same', text: A[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'del', text: A[i] });
      i++;
    } else {
      out.push({ kind: 'add', text: B[j] });
      j++;
    }
  }
  while (i < m) out.push({ kind: 'del', text: A[i++] });
  while (j < n) out.push({ kind: 'add', text: B[j++] });
  return out;
}

interface TreeNode {
  plugin: LabPlugin;
  children: TreeNode[];
}

function buildForest(plugins: LabPlugin[]): TreeNode[] {
  const byName = new Map<string, TreeNode>();
  for (const p of plugins) {
    // First occurrence of a name wins the tree slot (live plugin over candidate).
    if (!byName.has(p.name)) byName.set(p.name, { plugin: p, children: [] });
  }
  const roots: TreeNode[] = [];
  for (const node of byName.values()) {
    const parentName = node.plugin.lineageParent;
    const parent = parentName ? byName.get(parentName) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

function TreeRows({
  nodes,
  depth,
  selectedKey,
  onSelect,
}: {
  nodes: TreeNode[];
  depth: number;
  selectedKey: string | null;
  onSelect: (p: LabPlugin) => void;
}) {
  return (
    <>
      {nodes.map((node) => (
        <div key={node.plugin.key}>
          <button
            className={`lab-tree-row ${selectedKey === node.plugin.key ? 'sel' : ''}`}
            style={{ paddingLeft: 6 + depth * 16 }}
            onClick={() => onSelect(node.plugin)}
          >
            {depth > 0 && <span className="lab-tree-branch">└</span>}
            <span className={`lab-dot ${fateClass(node.plugin.fate)}`} />
            <span className="lab-tree-name">{node.plugin.name}</span>
            <span className="lab-tree-fate">{node.plugin.fate}</span>
          </button>
          {node.children.length > 0 && (
            <TreeRows
              nodes={node.children}
              depth={depth + 1}
              selectedKey={selectedKey}
              onSelect={onSelect}
            />
          )}
        </div>
      ))}
    </>
  );
}

export function CodeLab() {
  const labFocus = useStore((s) => s.labFocus);
  const openLab = useStore((s) => s.openLab);
  const [open, setOpen] = useState(false);
  const [plugins, setPlugins] = useState<LabPlugin[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('source');
  const [filter, setFilter] = useState<string>('all');

  const load = () => {
    void fetchJson<LabPlugin[]>('/api/lab/plugins')
      .then(setPlugins)
      .catch(() => undefined);
  };

  // Deep-link: opening focus from elsewhere (inspector, evolution panel).
  useEffect(() => {
    if (labFocus === null) return;
    setOpen(true);
    setSelectedKey(labFocus);
    load();
  }, [labFocus]);

  useEffect(() => {
    if (open) load();
  }, [open]);

  const selected = useMemo(
    () => plugins.find((p) => p.key === selectedKey) ?? null,
    [plugins, selectedKey],
  );
  const parent = useMemo(() => {
    if (!selected?.lineageParent) return null;
    return plugins.find((p) => p.name === selected.lineageParent) ?? null;
  }, [plugins, selected]);

  const fates = useMemo(
    () => ['all', ...Array.from(new Set(plugins.map((p) => p.fate))).sort()],
    [plugins],
  );
  const visible = filter === 'all' ? plugins : plugins.filter((p) => p.fate === filter);
  const forest = useMemo(() => buildForest(plugins), [plugins]);

  const close = () => {
    setOpen(false);
    openLab(null);
  };

  if (!open) {
    return (
      <button className="lab-launch" onClick={() => setOpen(true)} title="Open code lab">
        CODE LAB
      </button>
    );
  }

  return (
    <div className="lab-backdrop" onClick={close}>
      <div className="lab-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lab-header">
          CODE LAB
          <button className="lab-refresh" onClick={load} title="Refresh">
            ↻
          </button>
          <button className="lab-close" onClick={close} title="Close">
            ×
          </button>
        </div>

        <div className="lab-body">
          <div className="lab-browser">
            <div className="lab-filters">
              {fates.map((f) => (
                <button
                  key={f}
                  className={`lab-filter ${filter === f ? 'active' : ''}`}
                  onClick={() => setFilter(f)}
                >
                  {f}
                </button>
              ))}
            </div>
            <div className="lab-list">
              {visible.length === 0 && <div className="lab-empty">no plugins</div>}
              {visible.map((p) => (
                <button
                  key={p.key}
                  className={`lab-row ${selectedKey === p.key ? 'sel' : ''}`}
                  onClick={() => setSelectedKey(p.key)}
                >
                  <span className={`lab-dot ${fateClass(p.fate)}`} />
                  <span className="lab-row-name">{p.name}</span>
                  {p.fitness !== null && (
                    <span className="lab-row-fit">{p.fitness.toFixed(2)}</span>
                  )}
                  <span className="lab-row-fate">{p.fate}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="lab-detail">
            <div className="lab-tabs">
              <button
                className={`lab-tab ${tab === 'source' ? 'active' : ''}`}
                onClick={() => setTab('source')}
              >
                source
              </button>
              <button
                className={`lab-tab ${tab === 'diff' ? 'active' : ''}`}
                onClick={() => setTab('diff')}
              >
                diff vs parent
              </button>
              <button
                className={`lab-tab ${tab === 'tree' ? 'active' : ''}`}
                onClick={() => setTab('tree')}
              >
                phylogeny
              </button>
            </div>

            {tab === 'source' && (
              <pre className="lab-code">
                {selected ? selected.source : 'select a plugin'}
              </pre>
            )}

            {tab === 'diff' && (
              <div className="lab-code lab-diff">
                {!selected && 'select a plugin'}
                {selected && !parent && (
                  <span className="lab-empty">
                    no lineage parent{selected.lineageParent ? ` (${selected.lineageParent} not found)` : ''} — this is a root plugin
                  </span>
                )}
                {selected &&
                  parent &&
                  lineDiff(parent.source, selected.source).map((ln, idx) => (
                    <div key={idx} className={`lab-diff-line ${ln.kind}`}>
                      <span className="lab-diff-gutter">
                        {ln.kind === 'add' ? '+' : ln.kind === 'del' ? '-' : ' '}
                      </span>
                      {ln.text || ' '}
                    </div>
                  ))}
              </div>
            )}

            {tab === 'tree' && (
              <div className="lab-tree">
                {forest.length === 0 && <div className="lab-empty">no plugins</div>}
                <TreeRows
                  nodes={forest}
                  depth={0}
                  selectedKey={selectedKey}
                  onSelect={(p) => {
                    setSelectedKey(p.key);
                    setTab('source');
                  }}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
