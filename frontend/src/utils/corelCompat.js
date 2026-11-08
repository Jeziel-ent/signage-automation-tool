// Can the CorelDRAW on the designer's PC open this master? The site reads the version that saved the master from the file itself
// (`core_major`: 25 = CorelDRAW 2024, 21 = 2019), the agent reports which versions its PC has; an older CorelDRAW cannot open a newer file.

/** The newest CorelDRAW major version among the installs the page can see: the agent on THIS PC, else the user's online agents. 0 = unknown. */
export function maxCorelMajor(local, agents) {
  const installs = local ? local.corel || [] : (agents || []).filter((a) => a.online).flatMap((a) => a.corel || []);
  const majors = installs.map((c) => Number(c.major)).filter((m) => m > 0);
  return majors.length ? Math.max(...majors) : 0;
}

/** The CorelDRAW version this master needs when the PC's newest is older, else 0 (fine, or not enough information to say). */
export function masterNeeds(master, maxMajor) {
  const need = Number(master?.core_major) || 0;
  return need && maxMajor && maxMajor < need ? need : 0;
}
