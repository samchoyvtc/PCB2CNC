/** Board settings form helpers. */

import { getSelectedToolCuts, getSelectedToolNumber } from "./tools.js";

const SETTINGS_FORM_IDS = [
  "board_width_mm",
  "board_length_mm",
  "copper_tool_number",
  "engraving_depth_mm",
  "drill_depth_mm",
  "safe_z_mm",
  "retract_z_mm",
];

export function snapshotSettingsForm(form) {
  const values = {};
  if (!form) return values;
  for (const id of SETTINGS_FORM_IDS) {
    const el = form.elements[id];
    if (el) values[id] = el.value;
  }
  return values;
}

export function restoreSettingsForm(form, values) {
  if (!form || !values) return;
  for (const id of SETTINGS_FORM_IDS) {
    const el = form.elements[id];
    if (el && Object.prototype.hasOwnProperty.call(values, id)) el.value = values[id];
  }
}

export function settingsFormChanged(before, after) {
  if (!before || !after) return true;
  return SETTINGS_FORM_IDS.some((id) => String(before[id] ?? "") !== String(after[id] ?? ""));
}

export function readSettings(form) {
  const num = (id) => {
    const el = form.elements[id];
    return el ? Number(el.value) : NaN;
  };
  const cuts = getSelectedToolCuts();
  const throughDepth = (() => {
    const n = num("drill_depth_mm");
    return Number.isFinite(n) && n > 0 ? n : 1.7;
  })();
  return {
    tool_number: getSelectedToolNumber(2),
    board_width_mm: Number.isFinite(num("board_width_mm")) ? num("board_width_mm") : 100,
    board_length_mm: Number.isFinite(num("board_length_mm")) ? num("board_length_mm") : 150,
    copper_tool_number: (() => {
      const n = num("copper_tool_number");
      return Number.isFinite(n) && n >= 1 ? n : 2;
    })(),
    engraving_depth_mm: Number.isFinite(num("engraving_depth_mm")) ? num("engraving_depth_mm") : 0.2,
    drill_depth_mm: throughDepth,
    outline_depth_mm: throughDepth,
    safe_z_mm: Number.isFinite(num("safe_z_mm")) ? num("safe_z_mm") : 15,
    retract_z_mm: Number.isFinite(num("retract_z_mm")) ? num("retract_z_mm") : 3,
    ...cuts,
  };
}
