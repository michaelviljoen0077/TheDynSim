import { useEffect, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { useStore } from '../state/store';

const NIGHT_SKY = new THREE.Color('#050912');
const DAWN_SKY = new THREE.Color('#c97b3f');
const DAY_SKY = new THREE.Color('#8fc3e8');
const OVERCAST = new THREE.Color('#5a6470');
const SUN_LOW = new THREE.Color('#ff9440');
const SUN_HIGH = new THREE.Color('#fff5e0');
const AMB_NIGHT = new THREE.Color('#31406b');
const AMB_DAY = new THREE.Color('#cfe0f5');
const tmpSky = new THREE.Color();

function smoothstep(x: number, a: number, b: number): number {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

export function SunLight() {
  const sunRef = useRef<THREE.DirectionalLight>(null);
  const ambRef = useRef<THREE.AmbientLight>(null);
  const scene = useThree((s) => s.scene);
  const size = useStore((s) => s.sync?.size ?? 128);

  useEffect(() => {
    scene.background = new THREE.Color(NIGHT_SKY);
    scene.fog = new THREE.FogExp2(NIGHT_SKY.getHex(), 0.9 / size);
    return () => {
      scene.background = null;
      scene.fog = null;
    };
  }, [scene, size]);

  useFrame(() => {
    const sun = sunRef.current;
    const amb = ambRef.current;
    if (!sun || !amb) return;
    const frame = useStore.getState().frame;
    const dayFrac = frame?.clock.dayFrac ?? 0.35;
    const precip = Math.min(1, Math.max(0, frame?.weather.precip ?? 0));

    // dayFrac 0 = midnight, 0.25 = dawn, 0.5 = noon: sun elevation is a sine.
    const theta = (dayFrac - 0.25) * Math.PI * 2;
    const elev = Math.sin(theta);
    const daylight = smoothstep(elev, -0.08, 0.3);
    const horizonGlow = Math.max(0, 1 - Math.abs(elev) / 0.28);

    const c = (size - 1) / 2;
    const r = size * 1.2;
    sun.position.set(c + Math.cos(theta) * r, elev * r, c + 0.35 * r);
    sun.target.position.set(c, 0, c);
    sun.target.updateMatrixWorld();
    sun.color.copy(SUN_LOW).lerp(SUN_HIGH, smoothstep(elev, 0.03, 0.5));
    sun.intensity = 1.6 * daylight * (1 - 0.4 * precip);
    sun.visible = elev > -0.06;

    amb.color.copy(AMB_NIGHT).lerp(AMB_DAY, daylight);
    amb.intensity = 0.22 + 0.5 * daylight;

    // Sky and fog share one colour so the horizon dissolves cleanly.
    tmpSky.copy(NIGHT_SKY).lerp(DAY_SKY, daylight);
    tmpSky.lerp(DAWN_SKY, horizonGlow * 0.55 * Math.max(0.15, daylight));
    tmpSky.lerp(OVERCAST, precip * 0.45 * daylight);
    if (scene.background instanceof THREE.Color) scene.background.copy(tmpSky);
    if (scene.fog) scene.fog.color.copy(tmpSky);
  });

  return (
    <>
      <ambientLight ref={ambRef} intensity={0.3} />
      <directionalLight ref={sunRef} intensity={1.2} position={[100, 100, 0]} />
    </>
  );
}
