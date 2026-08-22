/** Board settings form helpers. */

import { getSelectedToolCuts, getSelectedToolNumber } from "./tools.js";

export function readSettings(form) {
  const num = (id) => {
    const el = form.elements[id];
    return el ? Number(el.value) : NaN;
  };
  const cuts = getSelectedToolCuts();
  return {
    tool_number: getSelectedToolNumber(2),
    board_width_mm: Number.isFinite(num("board_width_mm")) ? num("board_width_mm") : 100,
    board_length_mm: Number.isFinite(num("board_length_mm")) ? num("board_length_mm") : 150,
    engraving_depth_mm: num("engraving_depth_mm"),
    drill_depth_mm: Number.isFinite(num("drill_depth_mm")) ? num("drill_depth_mm") : 1.6,
    safe_z_mm: Number.isFinite(num("safe_z_mm")) ? num("safe_z_mm") : 15,
    retract_z_mm: Number.isFinite(num("retract_z_mm")) ? num("retract_z_mm") : 3,
    ...cuts,
  };
}
