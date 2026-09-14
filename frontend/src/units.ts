/** Metres to feet, for what a person reads. Mirrors `app.ir.units`: the IR and the
 *  plan file stay metric, and only labels are converted — plot owners in India size a
 *  room as "12 by 14" and a house in square feet, while the bye-laws are metric. */

const METRES_PER_FOOT = 0.3048; // exact, by the 1959 international agreement
const INCHES_PER_FOOT = 12;

/** 12'4" — rounded to the nearest inch, with twelve inches carried into a foot. */
export function feetAndInches(metres: number): string {
  const inches = Math.round((Math.abs(metres) / METRES_PER_FOOT) * INCHES_PER_FOOT);
  const sign = metres < 0 && inches ? "-" : "";
  return `${sign}${Math.floor(inches / INCHES_PER_FOOT)}'${inches % INCHES_PER_FOOT}"`;
}

/** 2,400 sq ft — whole square feet. */
export function squareFeet(sqM: number): string {
  const sqFt = Math.round(sqM / (METRES_PER_FOOT * METRES_PER_FOOT));
  return `${sqFt.toLocaleString("en-IN")} sq ft`;
}
