import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { screenIdFor } from '../config/nav';

/* The shell's three columns, decided from the measured window width.
 *
 * This is deliberately JS and not a media query. The centre pane's usable
 * width is the window MINUS whichever rails happen to be up, and a table
 * folding its columns has to know that number — CSS can express "the window is
 * 1600px wide", never "this pane is 994px wide because both rails are up".
 * Every fold below is keyed off `paneWidth` for that reason.
 *
 * Three breakpoints, in the order things give way:
 *   1180  below this there is no room for a third column, so the right rail goes
 *   1080  the left rail drops its labels and becomes an icon rail
 *    900  the left rail goes entirely, and the header carries every destination
 */
const LayoutContext = createContext(null);

/* Where a third column earns its width. Not Records, Monitoring, Reports or
 * Users: those are tables, and the rail was pushing their last two columns
 * into a horizontal scroll. */
const RIGHT_RAIL_SCREENS = ['dashboard', 'chart', 'preassess', 'summary'];

function read() {
  return typeof window === 'undefined' ? 1440 : window.innerWidth;
}

export function LayoutProvider({ children }) {
  const [width, setWidth] = useState(read);
  // Which screen is showing decides whether there is a third column at all,
  // so the provider reads the route rather than being told about it — one
  // provider above the Routes, instead of one per route, which would refetch
  // the census on every navigation.
  const screenId = screenIdFor(useLocation().pathname);

  useEffect(() => {
    let frame = 0;
    const onResize = () => {
      // Coalesce to one measurement per frame: a drag-resize fires this
      // dozens of times a second and each one re-renders the whole shell.
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setWidth(read()));
    };
    window.addEventListener('resize', onResize);
    return () => { cancelAnimationFrame(frame); window.removeEventListener('resize', onResize); };
  }, []);

  const value = useMemo(() => {
    const threeCol = width >= 1180;
    const railMode = width < 900 ? 'hidden' : width < 1080 ? 'icons' : 'full';
    const gap = width < 1180 ? 12 : 16;
    const pad = width < 1180 ? 12 : 16;

    const leftRailW = railMode === 'hidden' ? 0 : railMode === 'icons' ? 60 : 234;
    const rightRailOn = threeCol && RIGHT_RAIL_SCREENS.includes(screenId);
    const rightRailW = rightRailOn ? (width >= 1500 ? 318 : 288) : 0;
    const paneWidth = Math.max(320, width - leftRailW - rightRailW - gap * 2 - pad * 2);

    return {
      width, paneWidth, threeCol, railMode, gap, pad,
      leftRailOn: railMode !== 'hidden',
      railLabels: railMode === 'full',
      leftRailWidth: leftRailW,
      rightRailOn,
      rightRailWidth: rightRailW,
      // Header. Its brand block tracks the rail so the two line up, but must
      // not reserve a 234px column once the rail is gone.
      brandWidth: railMode === 'hidden' ? 'auto' : leftRailW,
      brandTextOn: width >= 1080,
      identityOn: width >= 1080,
      utilityIconsOn: width >= 1024,
      searchWidth: width < 1180 ? 210 : width < 1400 ? 268 : 320,
      searchHintOn: width >= 1180,
      searchPanelWidth: width < 1180 ? 340 : 430,
      tabWidth: width < 1180 ? 62 : width < 1400 ? 74 : 86,
      // Column folds, keyed off the PANE and not the window.
      recordsCategoryCol: paneWidth >= 940,
      recordsPsychCol: paneWidth >= 780,
      /* Progress Monitoring carries three more columns than Records, so it
       * folds one step earlier at every stage — and these numbers are measured
       * against the live vocabulary, not guessed. The first pass used the
       * mockup's thresholds, whose strings were shorter than the real ones
       * ("Pre-Assessment · Family Tracing & Reunification" is 47 characters),
       * and the last two columns ran off the right edge at 1440. */
      monitorPsychCol: paneWidth >= 1290,
      monitorPaCol: paneWidth >= 1150,
      monitorLastCol: paneWidth >= 1010,
      monitorClassCol: paneWidth >= 890,
    };
  }, [width, screenId]);

  return <LayoutContext.Provider value={value}>{children}</LayoutContext.Provider>;
}

/* Safe outside the shell (the sign-in and survey pages render no rails), so
 * a component can read layout without caring whether it is inside one. */
export function useLayout() {
  return useContext(LayoutContext) || {
    width: 1440, paneWidth: 1100, threeCol: true, railMode: 'full', gap: 16, pad: 16,
    leftRailOn: true, railLabels: true, leftRailWidth: 234,
    rightRailOn: false, rightRailWidth: 0, brandWidth: 234,
    brandTextOn: true, identityOn: true, utilityIconsOn: true,
    searchWidth: 320, searchHintOn: true, searchPanelWidth: 430, tabWidth: 86,
    recordsCategoryCol: true, recordsPsychCol: true,
    monitorPsychCol: true, monitorPaCol: true, monitorLastCol: true, monitorClassCol: true,
  };
}
