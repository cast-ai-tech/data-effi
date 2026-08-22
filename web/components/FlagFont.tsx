"use client";

/**
 * Hace que las banderas se vean en Windows.
 *
 * THE PROBLEM
 * Windows ships colour emoji but NOT the country flags: the regional-indicator
 * pairs that make 🇨🇴 have no glyph, so Chrome falls back to drawing the two
 * letters - "CO". Every flag in this app looked like initials on Windows, while
 * being perfectly fine on macOS, iOS and Android.
 *
 * THE FIX
 * A font that covers ONLY that unicode range (Twemoji Country Flags, 77 kB),
 * served from `public/fonts` rather than a CDN, and installed only when the
 * browser proves it needs it: the polyfill paints one emoji to a canvas and
 * compares pixels. On macOS nothing is downloaded at all.
 *
 * WHY A COMPONENT AND NOT CSS
 * The check needs a canvas, so it has to run in the browser. Mounting it in the
 * root layout means every screen gets it without importing anything, and the 46
 * call sites of `countryFlag()` keep returning a plain string.
 */

import { useEffect } from "react";

export function FlagFont() {
  useEffect(() => {
    let cancelled = false;

    // Cargado dinámicamente para que no entre en el bundle de servidor: el
    // polyfill toca `document` en cuanto se importa su tipo en algunos setups.
    import("country-flag-emoji-polyfill")
      .then(({ polyfillCountryFlagEmojis }) => {
        if (cancelled) return;
        // La fuente vive en public/fonts: la del paquete apunta a jsdelivr por
        // defecto, y una pantalla no debería depender de un CDN ajeno para algo
        // tan básico como una bandera.
        polyfillCountryFlagEmojis(
          "Twemoji Country Flags",
          "/fonts/TwemojiCountryFlags.woff2",
        );
      })
      .catch(() => {
        // Si falla, las banderas se ven como las iniciales. Feo, no roto: no
        // hay nada que reportarle al usuario sobre esto.
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}
