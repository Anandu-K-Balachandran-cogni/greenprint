import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { compileAllocation } from '$lib/constructos/compiler';
import type { Allocation } from '$lib/constructos/engine';
import { analyseGreen, applyWindowFixes, compassOf, WFR, type Climate } from '$lib/constructos/green';

const load = (f: string): Allocation => JSON.parse(readFileSync(new URL(`./fixtures/constructos/${f}`, import.meta.url), 'utf8'));
const meta = { projectId: 'p1', name: 'Green house', link: { engineProjectId: 'e1', layoutRunId: 'r1', candidate: 0, briefVersion: 1, briefHash: 'sha256-x' } };
const CHENNAI: Climate = { city: 'Chennai', zone: 'warm_humid', rainfall_mm: 1400, sun_hours: 5.5, tariff: 7, currency: 'INR', hemisphere: 'north' };
const DELHI: Climate = { city: 'Delhi', zone: 'composite', rainfall_mm: 750, sun_hours: 5.3, tariff: 7.5, currency: 'INR', hemisphere: 'north' };

describe('compass', () => {
  it('maps plan directions from the road side', () => {
    expect(compassOf({ x: 0, y: 1 }, 'south')).toBe('south');
    expect(compassOf({ x: 1, y: 0 }, 'south')).toBe('east');
    expect(compassOf({ x: -1, y: 0 }, 'south')).toBe('west');
    expect(compassOf({ x: 0, y: -1 }, 'east')).toBe('west');
    expect(compassOf({ x: 1, y: 0 }, 'north')).toBe('west');
    expect(compassOf({ x: 1, y: 1 }, 'south')).toBe('south_east');
  });
});

describe.each(['single-floor-allocation.json', 'two-storey-allocation.json'])('green analysis of %s', (f) => {
  const { floors } = compileAllocation(load(f), meta).project;

  it('checks every habitable room against the climate target and fixes reach it', () => {
    for (const climate of [CHENNAI, DELHI]) {
      const r = analyseGreen(floors, climate, 'south');
      expect(r.rooms.length).toBeGreaterThan(3);
      for (const room of r.rooms) {
        expect(room.required_wfr).toBeCloseTo(WFR[climate.zone], 3);
        expect(room.daylight_ok).toBe(room.wfr >= room.required_wfr - 1e-6);
      }
      const fixes = r.rooms.flatMap(x => x.fix ? [x.fix] : []);
      const after = analyseGreen(applyWindowFixes(floors, fixes), climate, 'south');
      for (const room of r.rooms.filter(x => x.fix)) {
        const now = after.rooms.find(x => x.room_id === room.room_id)!;
        expect(now.wfr, room.name).toBeCloseTo(room.wfr_after_fix!, 2);
        if (room.fix!.kind === 'add') expect(now.cross_ventilation, room.name).toBe(true);
      }
      expect(after.score.total).toBeGreaterThanOrEqual(r.score.total);
    }
  });

  it('sizes water, solar and carbon sensibly', () => {
    const r = analyseGreen(floors, CHENNAI, 'south');
    expect(r.water.roof_m2).toBeGreaterThan(50);
    expect(r.water.harvest_litres_year).toBeCloseTo(r.water.roof_m2 * 1400 * 0.8, -4);
    expect(r.solar.kwp).toBeGreaterThan(1);
    expect(r.carbon.total_t).toBeGreaterThan(20);
    expect(r.carbon.swaps[0].saving_t).toBeGreaterThan(0);
    expect(r.score.total).toBeGreaterThan(0);
    expect(r.score.total).toBeLessThanOrEqual(100);
    // A wet climate needs more window than a composite one.
    expect(r.rooms.filter(x => x.daylight_ok).length).toBeLessThanOrEqual(analyseGreen(floors, DELHI, 'south').rooms.filter(x => x.daylight_ok).length);
    // Unknown orientation: no compass claims.
    expect(analyseGreen(floors, CHENNAI, null).rooms.every(x => x.window_facings.length === 0)).toBe(true);
  });
});
