import * as THREE from 'three';
import type { Overlay } from '../state/store';

// Terrain palette + per-cell colouring, shared by the flat plane renderer
// (Terrain.tsx) and the folded cube renderer (CubeTerrain.tsx) so both worlds
// tint identically. The logic here is byte-for-byte the flat renderer's
// original per-cell branch, lifted verbatim.

const C_WATERBED = new THREE.Color('#155084');
const C_DEEP = new THREE.Color('#092b4d');
const C_SAND = new THREE.Color('#c8b47e');   // beaches (low, near water)
const C_SOIL = new THREE.Color('#8a6b46');   // BARREN dry earth (no flora)
const C_GRASS = new THREE.Color('#3f8f4d');  // lush green (full flora)
const C_ROCK = new THREE.Color('#7d7f84');   // mountains
const C_PEAK = new THREE.Color('#b8bcc4');
const tmpGround = new THREE.Color();

export function smoothstep(x: number, a: number, b: number): number {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

// Blue -> cyan -> green -> yellow -> red ramp for the heat-map overlays.
function heat(out: THREE.Color, t: number): THREE.Color {
  const c = Math.min(1, Math.max(0, t));
  const r = Math.min(1, Math.max(0, 1.5 * c - 0.4));
  const g = Math.min(1, Math.max(0, 1 - Math.abs(c - 0.5) * 2));
  const b = Math.min(1, Math.max(0, 1 - 2 * c));
  return out.setRGB(r * 0.9 + 0.05, g * 0.9 + 0.05, b * 0.9 + 0.05);
}

/**
 * Colour a single terrain cell into `out`.
 * @param h      height 0..1
 * @param water  true if the cell is open water
 * @param flora  flora density 0..1, or null when no flora frame yet
 */
export function cellColor(
  out: THREE.Color,
  h: number,
  water: boolean,
  flora: number | null,
  seaLevel: number,
  overlay: Overlay,
): void {
  if (overlay === 'flora') {
    // Heat-map of flora density; open water stays dark blue for contrast.
    if (water) out.copy(C_DEEP);
    else heat(out, flora ?? 0);
  } else if (overlay === 'water') {
    // Heat-map of water depth below sea level; dry land dimmed.
    if (water) heat(out, Math.min(1, (seaLevel - h) / 0.3));
    else out.setRGB(0.08, 0.1, 0.13);
  } else if (water) {
    out.copy(C_WATERBED).lerp(C_DEEP, smoothstep(seaLevel - h, 0, 0.25));
  } else {
    // Barren by default; green ONLY where flora actually grows. Beaches stay
    // sand and mountains stay grey regardless (those are elevation, not flora).
    // Flora is amplified a little so vegetated ground reads clearly green even at
    // the modest densities grazed land settles at.
    const f = Math.min(1, (flora ?? 0) * 1.8);
    const ground = tmpGround.copy(C_SOIL).lerp(C_GRASS, f);
    if (h < seaLevel + 0.03) {
      out.copy(C_SAND);
    } else if (h < 0.6) {
      out.copy(C_SAND).lerp(ground, smoothstep(h, seaLevel + 0.03, seaLevel + 0.12));
    } else if (h < 0.78) {
      out.copy(ground).lerp(C_ROCK, smoothstep(h, 0.6, 0.78));
    } else {
      out.copy(C_ROCK).lerp(C_PEAK, smoothstep(h, 0.78, 0.95));
    }
  }
}
