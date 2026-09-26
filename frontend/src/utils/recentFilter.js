/** Does a "recently generated" row match the search box? Shop name, master filename or any generated file name,
 *  case-insensitive substring; a blank term matches everything. */
export function matchesSearch(row, term) {
  const t = String(term || "").trim().toLowerCase();
  if (!t) return true;
  return [row.name, row.master_filename, ...Object.values(row.files || {})].some((v) => String(v || "").toLowerCase().includes(t));
}
