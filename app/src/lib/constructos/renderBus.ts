// The room panel asks the 3D viewer for a photo-real render; the viewer captures its own model
// (a room from eye height, or the current view) and the engine renders it with Gemini.
import { writable } from 'svelte/store';

export interface RenderRequest { kind: 'room' | 'view'; roomId?: string; roomName?: string; style?: string; nonce: number }
export interface RenderState { busy: boolean; error?: string; image?: string; label?: string; model?: string }

export const renderRequest = writable<RenderRequest | null>(null);
export const renderState = writable<RenderState>({ busy: false });
/** The look of the last kept interior design, passed to the render as its style. */
export const designStyle = writable<string | null>(null);

export function requestRender(r: Omit<RenderRequest, 'nonce'>) {
  renderRequest.set({ ...r, nonce: Date.now() });
}
