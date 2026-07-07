import { useEffect, useState } from 'react';
import { useStore } from '../state/store';
import { fetchJson, STRATUM_NAMES, type EntityDetail } from '../net/api';
import './inspector.css';

const POLL_MS = 1000;

export function InspectorPanel() {
  const selected = useStore((s) => s.selectedEntity);
  const selectEntity = useStore((s) => s.selectEntity);
  const openLab = useStore((s) => s.openLab);
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    if (selected === null) {
      setDetail(null);
      setGone(false);
      return;
    }
    const poll = () => {
      void fetchJson<EntityDetail>(`/api/entity/${selected}`)
        .then((d) => {
          if (d.error !== undefined) {
            setGone(true);
          } else {
            setDetail(d);
            setGone(false);
          }
        })
        .catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(timer);
  }, [selected]);

  if (selected === null) return null;

  return (
    <div className="inspector-panel">
      <div className="inspector-header">
        ENTITY INSPECTOR
        <button className="inspector-close" onClick={() => selectEntity(null)} title="Close">
          ×
        </button>
      </div>
      {gone && <div className="inspector-gone">entity no longer alive</div>}
      {!gone && detail && (
        <>
          <div className="inspector-species">{detail.species}</div>
          <div className="inspector-grid">
            <span className="k">id</span>
            <span className="v">{detail.id}</span>
            <span className="k">plugin</span>
            <span className="v">{detail.plugin || '—'}</span>
            <span className="k">energy</span>
            <span className="v">{detail.energy.toFixed(1)}</span>
            <span className="k">age</span>
            <span className="v">{detail.age}</span>
            <span className="k">stratum</span>
            <span className="v">{STRATUM_NAMES[detail.stratum] ?? detail.stratum}</span>
            <span className="k">pos</span>
            <span className="v">
              {detail.x.toFixed(1)}, {detail.y.toFixed(1)}, {detail.z.toFixed(1)}
            </span>
          </div>
          {detail.plugin && (
            <button
              className="inspector-lab"
              onClick={() => openLab(`live:${detail.plugin}`)}
              title="Open this entity's plugin in the code lab"
            >
              view plugin source →
            </button>
          )}
        </>
      )}
      {!gone && !detail && <div className="inspector-gone">loading…</div>}
    </div>
  );
}
