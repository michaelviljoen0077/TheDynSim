import { useCallback, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { heightAt, live } from '../net/frames';
import { useStore, type SpeciesInfo } from '../state/store';

const CAPACITY = 20000; // instances per species; draw count is set per frame
const tmpMatrix = new THREE.Matrix4();
const counters = new Map<number, number>();

function SpeciesMesh({
  info,
  meshes,
}: {
  info: SpeciesInfo;
  meshes: Map<number, THREE.InstancedMesh>;
}) {
  const ref = useCallback(
    (m: THREE.InstancedMesh | null) => {
      if (m) {
        m.count = 0;
        m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        meshes.set(info.id, m);
      } else {
        meshes.delete(info.id);
      }
    },
    [info.id, meshes],
  );
  const radius = info.size > 0 ? info.size : 0.5;
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, CAPACITY]} frustumCulled={false}>
      <sphereGeometry args={[radius, 10, 8]} />
      <meshStandardMaterial color={info.color} roughness={0.55} metalness={0.05} />
    </instancedMesh>
  );
}

export function Entities() {
  const species = useStore((s) => s.sync?.species);
  const meshes = useRef(new Map<number, THREE.InstancedMesh>());

  useFrame(() => {
    const map = meshes.current;
    const curr = live.curr;
    if (!curr) {
      map.forEach((m) => (m.count = 0));
      return;
    }
    const { hiddenSpecies, strata, sync } = useStore.getState();
    const heightScale = sync?.heightScale ?? 24;
    const prev = live.prev;
    // Render between the last two frames: alpha in [0,1] over the frame interval.
    const alpha = prev
      ? Math.min(1, (performance.now() - curr.recvTime) / live.frameInterval)
      : 1;

    counters.clear();
    for (let i = 0; i < curr.n; i++) {
      const spId = curr.species[i];
      const mesh = map.get(spId);
      if (!mesh || hiddenSpecies[spId]) continue;
      const stratum = curr.stratum[i];
      if (!strata[stratum]) continue;

      let x = curr.x[i];
      let y = curr.y[i];
      let z = curr.z[i];
      const j = curr.prevIndex[i];
      if (prev && j >= 0) {
        x = prev.x[j] + (x - prev.x[j]) * alpha;
        y = prev.y[j] + (y - prev.y[j]) * alpha;
        z = prev.z[j] + (z - prev.z[j]) * alpha;
      }
      const ground = heightAt(x, y) * heightScale;
      // Stratum band offsets: surface hugs the ground, sky floats in a band,
      // underground sits just below the crust (visible in x-ray mode).
      const worldY =
        stratum === 1 ? ground + 0.4 : stratum === 2 ? ground + 6 + z : ground - 2;

      const k = counters.get(spId) ?? 0;
      if (k >= CAPACITY) continue;
      tmpMatrix.makeTranslation(x, worldY, y);
      mesh.setMatrixAt(k, tmpMatrix);
      counters.set(spId, k + 1);
    }
    map.forEach((m, id) => {
      m.count = counters.get(id) ?? 0;
      m.instanceMatrix.needsUpdate = true;
    });
  });

  if (!species) return null;
  return (
    <group>
      {species.map((sp) => (
        <SpeciesMesh key={sp.id} info={sp} meshes={meshes.current} />
      ))}
    </group>
  );
}
