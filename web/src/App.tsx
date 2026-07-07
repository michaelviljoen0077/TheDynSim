import { WorldCanvas } from './world/WorldCanvas';
import { Hud } from './ui/Hud';
import { EvolutionPanel } from './ui/EvolutionPanel';
import { MetricsPanel } from './ui/MetricsPanel';

export function App() {
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <WorldCanvas />
      <Hud />
      <EvolutionPanel />
      <MetricsPanel />
    </div>
  );
}
