/** A subtle two-note ring for incoming calls, made with Web Audio (no audio file). Browsers may block sound until the
 * user has interacted with the page; then ``start`` reports false and the banner still shows. */
export interface Ringer {
  start(): Promise<boolean>;
  stop(): void;
}

export class WebAudioRinger implements Ringer {
  private context: AudioContext | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  async start(): Promise<boolean> {
    if (this.timer) return true;
    const Ctor = (window as unknown as { AudioContext?: typeof AudioContext }).AudioContext;
    if (!Ctor) return false;
    try {
      this.context = this.context ?? new Ctor();
      if (this.context.state === "suspended") await this.context.resume();
      if (this.context.state !== "running") return false;
    } catch {
      return false;
    }
    const ring = () => {
      const ctx = this.context;
      if (!ctx) return;
      [0, 0.25].forEach((offset, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = i === 0 ? 880 : 660;
        gain.gain.setValueAtTime(0.0001, ctx.currentTime + offset);
        gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + offset + 0.22);
        osc.connect(gain).connect(ctx.destination);
        osc.start(ctx.currentTime + offset);
        osc.stop(ctx.currentTime + offset + 0.24);
      });
    };
    ring();
    this.timer = setInterval(ring, 2000);
    return true;
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}

export const silentRinger: Ringer = { start: async () => false, stop: () => undefined };
