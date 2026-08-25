/**
 * The words the screen uses, explained once.
 *
 * Every term a first-time operator could stumble on lives here, so a widget
 * title, a table header and the onboarding all say the same thing about it.
 * Rendered by `HelpTip` (a "?" that opens by tap, so it works on a phone
 * where `title=` tooltips do not exist).
 *
 * Vocabulary rule: keep the words the operator already uses - guía, novedad,
 * devolución, flete, recaudo, transportadora, pauta - and translate the ones
 * that only an analyst says: maduración, cohorte, p90, tier.
 */

import { STATUS_GROUP_HINTS } from "@/lib/status";

export interface GlossaryEntry {
  /** The word as it appears on screen. */
  term: string;
  /** One or two plain sentences. No jargon inside the explanation. */
  short: string;
}

export const GLOSSARY = {
  guia: {
    term: "Guía",
    short: "Cada paquete que despachas. Una venta enviada = una guía.",
  },
  novedad: {
    term: "Novedad",
    short: STATUS_GROUP_HINTS.novedad,
  },
  devolucion: {
    term: "Devolución",
    short: STATUS_GROUP_HINTS.devolucion,
  },
  indemnizacion: {
    term: "Indemnización",
    short: STATUS_GROUP_HINTS.indemnizacion,
  },
  contraentrega: {
    term: "Contraentrega",
    short:
      "El cliente paga cuando recibe el paquete. Si no lo recibe, no hay venta y tú pagas el flete de ida y de vuelta.",
  },
  maduracion: {
    term: "Días de espera",
    short:
      "Cuántos días hay que esperar para saber si una guía se entregó o se devolvió. Hasta que pasan, el porcentaje de entrega de ese día es provisional.",
  },
  cohorte: {
    term: "Semana de despacho",
    short:
      "Las guías que salieron la misma semana, miradas juntas para ver cómo se van entregando día a día.",
  },
  tier: {
    term: "Tipo de conexión",
    short:
      "Cómo entra la información: por una conexión oficial, por archivo que subes, o entrando con tu usuario y contraseña (riesgo alto).",
  },
  contribucion: {
    term: "Contribución",
    short:
      "Lo que te queda después de restar producto, flete, comisiones y pauta. Si es negativa, estás pagando por vender.",
  },
  flete: {
    term: "Flete",
    short:
      "Lo que cobra la transportadora por llevar el paquete. En contraentrega, la devolución también cobra flete.",
  },
  liquidacion: {
    term: "Liquidación",
    short:
      "El pago que la plataforma o la transportadora te hace por las guías entregadas, ya con sus descuentos.",
  },
  recaudo: {
    term: "Recaudo",
    short: "La plata que la transportadora le cobró al cliente y te debe entregar.",
  },
  p90: {
    term: "Días (90 %)",
    short:
      "El 90 % de las guías se entregó en este tiempo o menos. Es más honesto que el promedio: no lo esconde una entrega rapidísima.",
  },
  muestra_corta: {
    term: "Estimado (~)",
    short:
      "Menos de 10 guías cerradas en este rango: el porcentaje es una idea, no una medición. Amplía el rango para comparar.",
  },
  capital_en_calle: {
    term: "Plata en la calle",
    short:
      "El valor de los paquetes que ya salieron y todavía no te han pagado. Es tu dinero viajando.",
  },
} as const satisfies Record<string, GlossaryEntry>;

export type GlossaryKey = keyof typeof GLOSSARY;

/** How the information gets in. Shown wherever the API says `tier`. */
export const TIER_LABELS: Record<number, string> = {
  1: "Conexión oficial",
  2: "Por archivo",
  3: "Con tu usuario y contraseña (riesgo alto)",
};

/** What each dashboard tab is about, for the "?" next to it. */
export const TAB_HELP: Record<string, string> = {
  finanzas: "Cuánto entra, cuánto sale y cuánto te queda. Recaudo, fletes, pauta y plata en la calle.",
  logistica: "Cómo van las entregas: por transportadora, por día y por plataforma.",
  efectividad: "Qué productos se entregan bien y dejan margen, y cuáles te están costando plata.",
  servicio: "Qué pasa con las guías que se frenan: confirmaciones, novedades y rescates en oficina.",
};
