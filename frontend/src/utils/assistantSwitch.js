/* What is asked before the agency-wide assistant switch is thrown.
 *
 * Two screens carry that switch: Settings, and the ISA's admin console on the
 * Dashboard. It stops briefs, summaries, remark polishing and the chat panel
 * for everyone, so both ask in the same words. They come from here rather than
 * being written twice: two copies drift, and the one that drifts is the one
 * that asks for less than the other.
 *
 *   const ok = await confirm(assistantSwitchConfirm(false));
 */
export function assistantSwitchConfirm(on) {
  return {
    description: on
      ? 'This turns the assistant on for everyone in the agency.'
      : 'This turns the assistant off for everyone in the agency — briefs, summaries, remark polishing and the chat panel stop until it is turned on again.',
    confirmLabel: on ? 'Yes, turn it on' : 'Yes, turn it off',
    tone: on ? 'brand' : 'warning',
  };
}
