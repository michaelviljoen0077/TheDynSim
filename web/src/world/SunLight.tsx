import { useEffect, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { useStore } from '../state/store';

const NIGHT_SKY = new THREE.Color('#16203c');
const MOON = new THREE.Color('#9fb4de');
const DAWN_SKY = new THREE.Color('#c97b3f');
const DAY_SKY = new THREE.Color('#8fc3e8');
const OVERCAST = new THREE.Color('#5a6470');
const SUN_LOW = new THREE.Color('#ff9440');
const SUN_HIGH = new THREE.Color('#fff5e0');
const AMB_NIGHT = new THREE.Color('#7285b8');
const AMB_DAY = new THREE.Color('#cfe0f5');
const tmpSky = new THREE.Color();

function smoothstep(x: number, a: number, b: number): number {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

export function SunLight() {
  const sunRef = useRef<THREE.DirectionalLight>(null);
  const moonRef = useRef<THREE.DirectionalLight>(null);
  const ambRef = useRef<THREE.AmbientLight>(null);
  const scene = useThree((s) => s.scene);
  const size = useStore((s) => s.sync?.size ?? 128);
  const isCube = useStore((s) => s.sync?.topology === 'cube');

  useEffect(() => {
    scene.background = new THREE.Color(NIGHT_SKY);
    // Flat world hugs the ground under dense fog; the cube/globe is viewed from
    // orbit, so fog stays light enough to keep the whole sphere visible.
    scene.fog = new THREE.FogExp2(NIGHT_SKY.getHex(), (isCube ? 0.12 : 0.9) / size);
    return () => {
      scene.background = null;
      scene.fog = null;
    };
  }, [scene, size, isCube]);

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

    // Flat world is centred at (c,0,c); the cube/globe is centred at the origin.
    const c = isCube ? 0 : (size - 1) / 2;
    const r = size * 1.2;
    sun.position.set(c + Math.cos(theta) * r, elev * r, c + 0.35 * r);
    sun.target.position.set(c, 0, c);
    sun.target.updateMatrixWorld();
    sun.color.copy(SUN_LOW).lerp(SUN_HIGH, smoothstep(elev, 0.03, 0.5));
    sun.intensity = 1.6 * daylight * (1 - 0.4 * precip);
    sun.visible = elev > -0.06;

    amb.color.copy(AMB_NIGHT).lerp(AMB_DAY, daylight);
    // night floor kept generous: the world must stay watchable at midnight —
    // this reads as "bright moonlit night", not darkness
    amb.intensity = 0.75 + 0.25 * daylight;

    // moon: cool fill from opposite the sun, fading out as daylight rises
    const moon = moonRef.current;
    if (moon) {
      moon.position.set(c - Math.cos(theta) * r, Math.max(0.25, -elev) * r, c - 0.35 * r);
      moon.target.position.set(c, 0, c);
      moon.target.updateMatrixWorld();
      moon.color.copy(MOON);
      moon.intensity = 1.0 * (1 - daylight) * (1 - 0.3 * precip);
      moon.visible = daylight < 0.85;
    }

    // Sky and fog share one colour so the horizon dissolves cleanly.
    tmpSky.copy(NIGHT_SKY).lerp(DAY_SKY, daylight);
    tmpSky.lerp(DAWN_SKY, horizonGlow * 0.55 * Math.max(0.15, daylight));
    tmpSky.lerp(OVERCAST, precip * 0.45 * daylight);
    if (scene.background instanceof THREE.Color) scene.background.copy(tmpSky);
    if (scene.fog) scene.fog.color.copy(tmpSky);
  });

  return (
    <>
      <ambientLight ref={ambRef} intensity={0.5} />
      <directionalLight ref={sunRef} intensity={1.2} position={[100, 100, 0]} />
      <directionalLight ref={moonRef} intensity={0.5} position={[-100, 100, 0]} />
    </>
  );
}
