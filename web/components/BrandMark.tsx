/**
 * The mark: a blue tile with "MD" and, next to it, the name.
 *
 * One component for the three places it appears (sidebar, login, onboarding)
 * so the brand cannot drift between screens. The container carries the name
 * for assistive tech; the letters inside are decoration.
 */

import { cx } from "@/components/ui/cx";

export function BrandMark({
  size = "sm",
  wordmark = true,
  className,
}: {
  size?: "sm" | "md";
  /** Show "Master Data" next to the tile. Off in the collapsed sidebar. */
  wordmark?: boolean;
  className?: string;
}) {
  const tile = size === "md" ? "size-9 text-lg" : "size-8 text-md";
  const name = size === "md" ? "text-2xl" : "text-lg";
  return (
    <div className={cx("flex items-center gap-2.5", className)} aria-label="Master Data">
      <div
        aria-hidden
        className={cx(
          "flex shrink-0 items-center justify-center rounded-control bg-accent font-extrabold tracking-tight text-on-accent",
          tile,
        )}
      >
        MD
      </div>
      {wordmark && (
        <span className={cx("whitespace-nowrap font-bold tracking-tight text-ink", name)}>
          Master Data
        </span>
      )}
    </div>
  );
}
