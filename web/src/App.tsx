import { WorldCanvas } from './world/WorldCanvas';
import { Hud } from './ui/Hud';

export function App() {
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <WorldCanvas />
      <Hud />
    </div>
  );
}
