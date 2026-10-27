/* What is asked before a test text is sent.
 *
 * Two screens send one: Settings, and the ISA's admin console on the
 * Dashboard. A test text is a real message through the gateway and uses a
 * credit, so both ask in the same words, and both point to the free way to
 * check the key. They come from here rather than being written twice: two
 * copies drift, and the one that drifts is the one that asks for less.
 *
 *   const ok = await confirm(smsTestConfirm());
 */
export function smsTestConfirm() {
  return {
    title: 'Send a test text to your own verified number?',
    description: 'This sends one real text message to your own confirmed number, through the SMS provider, and uses 1 credit. "Check the key" only checks the key and is free.',
    confirmLabel: 'Yes, send it',
  };
}
