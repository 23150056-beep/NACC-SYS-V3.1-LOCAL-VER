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

/* The header's search bar, an inch (96px) longer than it was at the owner's
 * request (12 Oct 2026): 306 under 1180, 364 to 1399, 416 from 1400. The tab
 * strip beside it shrinks to make the room, down to its 36px a tab.
 *
 * Only a narrow window runs out of room. Under 842 the "More" button costs the
 * strip 66px, so the bar takes what is spare - never less than it had - and
 * reaches the full inch at 842. From there it always fits, so it only ever
 * grows with the window. That is also why the account's name and role wait for
 * 1280 (`identityOn`): they cost 151px, and at 1080 there was no room left for
 * them AND the inch, so the bar had to shrink 96px as the window grew.
 * `spare` is worked out against the widest header there is (the ISA: six tabs,
 * the longest role label) with 30px to play with, so a different font does not
 * push a tab under the bar. */
function searchBarWidth(width) {
  const was = width < 1180 ? 210 : width < 1400 ? 268 : 320;
  return Math.min(was + 96, Math.max(was, width - 536));
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
      width, paneWidth,
      // Under 480 the header has no room for the search pill beside the tabs
      // (owner, 28 Oct 2026): the pill becomes a magnifier that opens the same
      // panel. Every size above 480 is as the owner set it.
      phone: width < 480, threeCol, railMode, gap, pad,
      leftRailOn: railMode !== 'hidden',
      railLabels: railMode === 'full',
      leftRailWidth: leftRailW,
      rightRailOn,
      rightRailWidth: rightRailW,
      // Header. Its brand block tracks the rail so the two line up, but must
      // not reserve a 234px column once the rail is gone.
      brandWidth: railMode === 'hidden' ? 'auto' : leftRailW,
      brandTextOn: width >= 1080,
      // The rail's account card names the signed-in user from 1080, so the
      // header repeats it only where it is cheap: 1280 leaves the tabs their
      // room even for a long first name.
      identityOn: width >= 1280,
      utilityIconsOn: width >= 1024,
      searchWidth: searchBarWidth(width),
      searchHintOn: width >= 1180,
      // Wider than the bar at every width (its longest is 416).
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
    width: 1440, phone: false, paneWidth: 1100, threeCol: true, railMode: 'full', gap: 16, pad: 16,
    leftRailOn: true, railLabels: true, leftRailWidth: 234,
    rightRailOn: false, rightRailWidth: 0, brandWidth: 234,
    brandTextOn: true, identityOn: true, utilityIconsOn: true,
    searchWidth: 416, searchHintOn: true, searchPanelWidth: 430, tabWidth: 86,
    recordsCategoryCol: true, recordsPsychCol: true,
    monitorPsychCol: true, monitorPaCol: true, monitorLastCol: true, monitorClassCol: true,
  };
}
