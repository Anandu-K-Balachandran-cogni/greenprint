import { env } from '$env/dynamic/private';
import { json } from '@sveltejs/kit';

// The Maps JavaScript key (restricted to this app's addresses) for signed-in users only.
export const GET = () => json({ key: env.MAPS_BROWSER_KEY || null });
